from .seesaw_bench import add_stats, get_param_hash
import pandas as pd
import math
from IPython.display import display
from plotnine import ggplot, aes, theme
from plotnine import *


def get_active_search_params2(tup):
    session_params = tup[0]
    variant = session_params['interactive']
    if session_params is None:
        print(f'{tup=}')
        
    opts = session_params.get('interactive_options', {})
    if opts is None:
        opts = {}
    gamma_opts = opts.get('gamma', {})

    ans = {}
    
    ans['reward_horizon'] = opts.get('reward_horizon', None) if variant in ['active_search'] else None

    keys = ['calibration']

    for k in keys:
        ans[k] = gamma_opts.get(k,None) if (type(gamma_opts) is dict) and (variant in ['active_search']) else None
    return ans


def get_multi_reg_params(tup):
    session_params = tup[0]
    variant = session_params['interactive']
    
    if session_params is None:
        print(f'{tup=}')
    
    keys = [ 'reg_data_lambda', 'reg_query_lambda', 'reg_norm_lambda', 'label_loss_type', 'pos_weight']
    opts = session_params.get('interactive_options', {})
    
    if opts is None:
        opts = {}
    ans = {}
    for k in keys:
        ans[k] = opts.get(k, None) if variant == 'multi_reg' else None
        
        
    matrix_options_keys = ['knn_k', 'edist', 'normalized_weights']
    matrix_opts = opts.get('matrix_options', {})
    for k in matrix_options_keys:
        ans[k] = matrix_opts.get(k,None) if variant == 'multi_reg' else None
        
    return ans
    
def get_matrix_params(tup):
    session_params = tup[0]
    opts = session_params.get('interactive_options', {})
       
    rets = ['knn_k', 'edist', 'knn_path']
    
    variant = session_params['interactive']
    if variant in ['knn_prop2', 'lknn', 'active_search']:
        opts= opts['matrix_options']
    elif variant == 'pseudo_lr':
        opts = opts['label_prop_params']['matrix_options']
    else:
        return dict(zip(rets, [None]*len(rets)))

    return {k:opts[k] for k in rets}
        
def get_reg_lambda(tup):
    session_params = tup[0]
    opts = session_params['interactive_options']

    if session_params.get('interactive', None) == 'log_reg2':
        return {'reg_lambda': opts.get('reg_lambda', None)}
    elif session_params.get('interactive', None) == 'pseudo_lr':
        return {'reg_lambda': opts.get('log_reg_params', {}).get('reg_lambda', None)}
    else:
        return {'reg_lambda': None}
    
def get_rocchio_params(tup):
    session_params = tup[0]
    opts = session_params.get('interactive_options',None)
    if opts is None:
        opts = {}
    
    rets = ['rocchio_alpha', 'rocchio_beta', 'rocchio_gamma']    
    return {k:opts.get(k, None) for k in rets}

def apply_funs(*funs):
    def fun(tup):
        ans = {}
        for f in funs:
            ans.update(f(tup))
    
        return ans
    
    return fun

def post_process(summs):
    stats = add_stats(summs)
    stats = stats.assign(param_hash=stats.session_params.map(get_param_hash))
    stats = stats.assign(full_hash= stats.param_hash + ('_' + stats.index_name))
    stats = stats.assign(url='http://localhost.localdomain:9001/session_info?path=' + stats.session_path)
    return stats


#  other_cols = ['variant', 'index_name', 'start_policy', 'knn_k', 'edist', 'calibration', 'reward_horizon', 'pos_weight', 'reg_data_lambda', 'reg_query_lambda', 'reg_norm_lambda', ]

# metric='reciprocal_rank' # 1/first result position. 1 is first result. 0 is not found.
def display_means(stats, *, other_cols=[], metric, return_totals=False):
    base_group_cols = ['index_name', 'param_hash', 'timestamp'] # makes sure we don't mix apples and oranges
    group_cols = [ c for c in base_group_cols if c not in other_cols]
    column_cols = ['dataset']

    cols = other_cols + group_cols  + column_cols

    totals = stats.groupby(cols, dropna=False)[metric].mean()
    by_dataset = totals.unstack(level=-1)
    avg = by_dataset.mean(axis=1)
    by_dataset = by_dataset.assign(average=avg)
    by_dataset = by_dataset.reset_index()
    by_dataset = by_dataset.sort_values(other_cols + group_cols, ascending=True)

    with pd.option_context('format.precision', 2):
        display(by_dataset.style.background_gradient(axis=0))
        
    if return_totals:
        return by_dataset
    
def make_clickable(val):
    # target _blank to open new window
    return '<a target="_blank" href="{}">{}</a>'.format(val, val.split('/')[-2])


def show_dataset_lines(stats, metric, param, variant, dataset = 'bdd', index_name = 'multiscalecoarse', ):
    stats = stats.query(f'dataset == "{dataset}" and index_name == "{index_name}"')
    baseline_data = stats.query(f'variant == "baseline"')
    
    imperfect_categories = set(baseline_data.query(f'{metric} < 1.').category.unique())
    
    stats = stats[stats.category.isin(imperfect_categories)]
    baseline_data = stats.query(f'variant == "baseline"')
    
    ncategories = stats.category.unique().shape[0]
    width = 5
    height = math.ceil(ncategories / width)

    return (ggplot(data=stats.query(f'variant == "{variant}"'))
         + geom_line(aes(x=param, y=metric))
          + geom_point(aes(x=param, y=metric))
            + facet_wrap(facets=['category'], ncol=width)# scales='free_y') 
            + geom_hline(aes(yintercept=metric, color='dataset'),  
                         data=baseline_data)
            + scale_x_log10()
            + ylim(0,1)
            + theme(figure_size=(width*1.8, height*1.2)))