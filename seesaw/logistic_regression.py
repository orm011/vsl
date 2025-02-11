
import torch
import numpy as np
from torch.utils.data import DataLoader
from torch.utils.data import TensorDataset
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as opt
from .basic_trainer import BasicTrainer
import numpy as np

import math
from sklearn.preprocessing import StandardScaler
from seesaw.rank_loss import cheap_pairwise_rank_loss

class RankingRegModule(nn.Module):
    def __init__(self, *, dim, reg_weight=1., verbose=False, max_iter=100, lr=1., regularizer_function=None):
        super().__init__()
        self.linear = nn.Linear(dim, 1, bias=False)
        self.regularizer_function = regularizer_function            
        self.reg_weight = reg_weight
        self.max_iter = max_iter
        self.lr = lr
        self.verbose = verbose
        
    def get_coeff(self):
        return self.linear.weight.detach().numpy()

    def forward(self, X):
        return self.linear(X)
    
    def _step(self, batch):
        if len(batch) == 2:
            X,y=batch # note y can be a floating point
            weight=None
        else:
            assert len(batch) == 3
            X,y,weight = batch
            raise NotImplementedError('handle weights later on')
            
        scores = self(X)
        return cheap_pairwise_rank_loss(y.reshape(-1), scores=scores.reshape(-1))
    
    def training_step(self, batch, batch_idx):
        inversions = self._step(batch)
        if self.regularizer_function is None:
            reg = self.linear.weight.norm()
        else:
            reg = self.regularizer_function()   
        
        mean_inversions = inversions.sum() # mean is taken within function value is less than 1. always
        total_loss = mean_inversions  + self.reg_weight*reg
        ret = {'loss':total_loss, 'weighted_reg_loss':self.reg_weight*reg.detach().item(), 'mean_inversion':mean_inversions.detach().item()}

        if self.verbose:
            print('wnorm', self.linear.weight.norm().detach().item())
            print(f'{ret=}')
        return ret

    def validation_step(self, batch, batch_idx):
        loss = self._step(batch)
        return {'loss':loss}

    def configure_optimizers(self):
        return opt.LBFGS(self.parameters(), max_iter=self.max_iter, lr=self.lr, line_search_fn='strong_wolfe')


class LogisticRegModule(nn.Module):
    def __init__(self, *, dim,  pos_weight=1., reg_weight=1., fit_intercept=True,  verbose=False, max_iter=100, lr=1., regularizer_function=None):
        super().__init__()
        self.linear = nn.Linear(dim, 1, bias=fit_intercept)
        self.pos_weight = torch.tensor([pos_weight])
        self.regularizer_function = regularizer_function
            
        self.reg_weight = reg_weight
        self.max_iter = max_iter
        self.lr = lr
        self.verbose = verbose
        
    def get_coeff(self):
        return self.linear.weight.detach().numpy()

    def forward(self, X, y=None):
        logits =  self.linear(X)
        if y is None:
            return logits.sigmoid()
        else:
            return logits
    
    def _step(self, batch):
        if len(batch) == 2:
            X,y=batch # note y can be a floating point
            weight=None
        else:
            assert len(batch) == 3
            X,y,weight = batch
            
        logits = self(X, y)
        weighted_celoss = F.binary_cross_entropy_with_logits(logits, y, weight=weight,
                                    reduction='none', pos_weight=self.pos_weight)
        return weighted_celoss
        
    
    def training_step(self, batch, batch_idx):
        celoss = self._step(batch).mean()
        if self.regularizer_function is None:
            reg = self.linear.weight.norm()
        else:
            reg = self.regularizer_function()
        
        loss = celoss + self.reg_weight*reg
        if self.verbose:
            print('wnorm', self.linear.weight.norm().detach().item())
            if self.linear.bias is not None:
                print('bias', self.linear.bias.detach().item())
        
        return {'loss':loss, 'celoss':celoss, 'reg':reg}
    
    def validation_step(self, batch, batch_idx):
        loss = self._step(batch)
        return {'loss':loss.mean()}

    def configure_optimizers(self):
        return opt.LBFGS(self.parameters(), max_iter=self.max_iter, lr=self.lr, line_search_fn='strong_wolfe')

class RankRegressionPT: 
    def __init__(self,  scale, reg_lambda,  
            regularizer_vector, verbose=False, **kwargs):
        ''' reg type: nparray means use that vector '''
        assert scale in ['centered', None]
        self.kwargs = kwargs
        self.model_ = None
        self.trainer_ = None
        self.mu_ = None
        self.scale = scale
        self.reg_lambda = reg_lambda
        self.n_examples = None
        self.regularization_type = None
        self.regularizer_vector = None
        self.verbose = verbose

        if isinstance(regularizer_vector, np.ndarray):
            self.regularizer_vector = F.normalize(torch.from_numpy(regularizer_vector.reshape(1,-1)).float(), dim=-1).reshape(-1)
            self.regularization_type = 'vector'
        elif isinstance(regularizer_vector, str):
            self.regularization_type = regularizer_vector
        elif regularizer_vector is None:
            self.regularization_type = None
        else:
            assert False
            

        if scale == 'centered':
            self.scaler_ = StandardScaler(with_mean=True, with_std=False)
        else:
            self.scaler_ = None

    def _regularizer_func(self):
        assert self.model_ is not None

        if self.regularization_type is None:
            return 0.
        
        weight = self.model_.linear.weight

        if self.regularizer_vector is not None:
            if self.scale == 'centered' or self.scale is None :
                base_vec = self.regularizer_vector
            else:
                assert False

            norm_penalty = (weight.norm() - 1.)**2
            angle_penalty = (F.normalize(weight).reshape(-1) - base_vec.reshape(-1)).norm()**2
        else:
            if self.regularization_type in ['norm1', 'norm']:
                norm_target = 1 if self.regularization_type == 'norm1' else 0
                norm_penalty = (weight.norm() - norm_target)**2
            else:
                assert False
                
            angle_penalty = 0.

        ans = norm_penalty + angle_penalty
        return ans 
            
    def _get_coeff(self):
        assert self.model_
        weight_prime = self.model_.linear.weight
        if self.scale == 'centered':
            return weight_prime
        else:
            assert False

    def get_coeff(self):
        return self._get_coeff().detach().numpy()

    def _get_intercept(self):
        assert self.model_
        return -self._get_coeff()@self.mu_.reshape(-1) + self.model_.linear.bias

    def get_intercept(self):
        return self._get_intercept().detach().numpy()
    
    # how many pairs of elements with different targets are there? n1*(n - n1) + n2*(n-n2) + ... + 
    # we want to divide the total inversions by that number.

    def fit(self, X, y, sample_weights=None):
        n_examples = X.shape[0]

        if self.scaler_:
            X = self.scaler_.fit_transform(X)
            self.mu_ = torch.from_numpy(self.scaler_.mean_).float()
        
        reg_weight = self.reg_lambda/n_examples

        if self.model_ is None:
            self.model_ = RankingRegModule(dim=X.shape[1], 
                            reg_weight=reg_weight,
                            ## for some reason the weight vector we get when we are forced to not use intercept is better than
                            ## when we use the intercept.
                            regularizer_function=self._regularizer_func, 
                            verbose=self.verbose,
                            **self.kwargs)

            # self.trainer_ = BasicTrainer(mod=self.model_, max_epochs=1)
            self.trainer_ = BasicTrainer(mod=self.model_, max_epochs=1)

        else: # warm start
            self.model_.reg_weight = reg_weight

        
        if self.regularizer_vector is None: 
            self.regularizer_vector = torch.zeros_like(self.model_.linear.weight)

        if sample_weights is not None:
            sample_weights = torch.from_numpy(sample_weights)
            ds = TensorDataset(torch.from_numpy(X), torch.from_numpy(y), sample_weights)
        else:
            ds = TensorDataset(torch.from_numpy(X), torch.from_numpy(y))
            
        dl = DataLoader(ds, batch_size=len(ds), shuffle=True)
        self.losses_ = self.trainer_.fit(dl)

        for i,loss in enumerate(self.losses_):
            if math.isnan(loss['loss']) or math.isinf(loss['loss']):
                print(f'warning: loss diverged at step {i=} {loss["loss"]=:.03f}')
                raise ValueError('regression training failed with a nan')
        
        niter = len(self.losses_)
        final_loss = self.losses_[-1]
        if self.verbose:
            print(f'regression converged after {niter} iterations. {final_loss=}')
                
    def predict_proba(self, X):
        if self.scaler_:
            X = self.scaler_.transform(X)
        
        with torch.no_grad():
            return self.model_(torch.from_numpy(X)).numpy()

    def predict_proba2(self, X): # test
        X = torch.from_numpy(X)
        with torch.no_grad():
            logits =  X @ self._get_coeff().reshape(-1)   + self._get_intercept()
            ps = logits.sigmoid()

        return ps.reshape(-1,1).numpy()


class LogisticRegressionPT: 
    def __init__(self, *, class_weights, scale,  reg_lambda,  
            regularizer_vector,  fit_intercept, verbose=False, **kwargs):
        ''' reg type: nparray means use that vector '''
        assert scale in ['centered', None]
        self.class_weights = class_weights
        self.kwargs = kwargs
        self.model_ = None
        self.trainer_ = None
        self.mu_ = None
        self.scale = scale
        self.reg_lambda = reg_lambda
        self.n_examples = None
        self.regularization_type = None
        self.regularizer_vector = None
        self.verbose = verbose
        self.fit_intercept = fit_intercept

        if isinstance(regularizer_vector, np.ndarray):
            self.regularizer_vector = F.normalize(torch.from_numpy(regularizer_vector.reshape(1,-1)).float(), dim=-1).reshape(-1)
            self.regularization_type = 'vector'
        elif isinstance(regularizer_vector, str):
            self.regularization_type = regularizer_vector
        elif regularizer_vector is None:
            self.regularization_type = None
        else:
            assert False
            

        if scale == 'centered':
            self.scaler_ = StandardScaler(with_mean=True, with_std=False)
        else:
            self.scaler_ = None

    def _regularizer_func(self):
        assert self.model_ is not None

        if self.regularization_type is None:
            return 0.
        
        weight = self.model_.linear.weight

        if self.regularizer_vector is not None:
            if self.scale == 'centered' or self.scale is None :
                base_vec = self.regularizer_vector
            else:
                assert False

            norm_penalty = (weight.norm() - 1.)**2
            angle_penalty = (F.normalize(weight).reshape(-1) - base_vec.reshape(-1)).norm()**2
        else:
            if self.regularization_type in ['norm1', 'norm']:
                norm_target = 1 if self.regularization_type == 'norm1' else 0
                norm_penalty = (weight.norm() - norm_target)**2
            else:
                assert False
                
            angle_penalty = 0.

        ans = norm_penalty + angle_penalty
        return ans 
            
    def _get_coeff(self):
        assert self.model_
        weight_prime = self.model_.linear.weight
        if self.scale == 'centered':
            return weight_prime
        else:
            assert False

    def get_coeff(self):
        return self._get_coeff().detach().numpy()

    def _get_intercept(self):
        assert self.model_
        return -self._get_coeff()@self.mu_.reshape(-1) + self.model_.linear.bias

    def get_intercept(self):
        return self._get_intercept().detach().numpy()
    
    def fit(self, X, y, sample_weights=None):
        n_examples = X.shape[0]

        if self.scaler_:
            X = self.scaler_.fit_transform(X)
            self.mu_ = torch.from_numpy(self.scaler_.mean_).float()

        if self.class_weights == 'balanced':
                npos = (y == 1).sum()
                nneg = (y == 0).sum() 
                pseudo_pos = max(npos, 1)
                pseudo_neg = max(nneg, 1)
                pos_weight = pseudo_neg / pseudo_pos
        else:
            pos_weight = self.class_weights
        
        reg_weight = self.reg_lambda/n_examples

        if self.model_ is None:
            self.model_ = LogisticRegModule(dim=X.shape[1], 
                            pos_weight=pos_weight, 
                            reg_weight=reg_weight,
                            fit_intercept=self.fit_intercept, 
                            ## for some reason the weight vector we get when we are forced to not use intercept is better than
                            ## when we use the intercept.
                            regularizer_function=self._regularizer_func, 
                            verbose=self.verbose,
                            **self.kwargs)

        else: # warm start
            assert self.class_weights != 'balanced', 'implement this case'
            self.model_.reg_weight = reg_weight

        # start from fresh trainer sinced not clear what the lbfgs opt does.
        self.trainer_ = BasicTrainer(mod=self.model_, max_epochs=1)
        
        if self.regularizer_vector is None: 
            self.regularizer_vector = torch.zeros_like(self.model_.linear.weight)

        if sample_weights is not None:
            sample_weights = torch.from_numpy(sample_weights)
            ds = TensorDataset(torch.from_numpy(X), torch.from_numpy(y), sample_weights)
        else:
            ds = TensorDataset(torch.from_numpy(X), torch.from_numpy(y))
            
        dl = DataLoader(ds, batch_size=len(ds), shuffle=True)
        self.losses_ = self.trainer_.fit(dl)

        for i,loss in enumerate(self.losses_):
            if math.isnan(loss['loss']) or math.isinf(loss['loss']):
                print(f'warning: loss diverged at step {i=} {loss["loss"]=:.03f}')
                raise ValueError('regression training failed with a nan')
        
        niter = len(self.losses_)
        final_loss = self.losses_[-1]
        if self.verbose:
            print(f'regression converged after {niter} iterations. {final_loss=}')
                
    def predict_proba(self, X):
        if self.scaler_:
            X = self.scaler_.transform(X)
        
        with torch.no_grad():
            return self.model_(torch.from_numpy(X)).numpy()

    def predict_proba2(self, X): # test
        X = torch.from_numpy(X)
        with torch.no_grad():
            logits =  X @ self._get_coeff().reshape(-1)   + self._get_intercept()
            ps = logits.sigmoid()

        return ps.reshape(-1,1).numpy()
