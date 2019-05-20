#Pair Trading Algorithm

import quantopian.algorithm as algo
from quantopian.pipeline import Pipeline,CustomFactor
from quantopian.pipeline.data.builtin import USEquityPricing
from quantopian.pipeline.filters import QTradableStocksUS
import numpy as np
import pandas as pd
import statsmodels.tsa.stattools as sm
from quantopian.pipeline.filters import Q500US, Q3000US
from quantopian.pipeline.data import Fundamentals
from quantopian.pipeline.classifiers.fundamentals import Sector
from quantopian.algorithm import attach_pipeline, pipeline_output
from collections import OrderedDict

COMMISSION = 0.0035
MAX_GROSS_EXPOSURE = 1.0

def initialize(context):
    
    context.stocks = Q500US()  
    pipe = Pipeline()
    pipe = attach_pipeline(pipe, name = 'pairs')
    pipe.set_screen(context.stocks)
     
    context.price_history_length = 365
    context.long_ma_length = 30
    context.short_ma_length = 1
    context.entry_threshold = 0.2
    context.exit_threshold = 0.1
    context.universe_size = 500
    context.num_pairs = 4
    context.coint_cutoff=0.05
    context.corr_cutoff=0.95
    context.top_yield_pairs = []
    context.increments=50
    
    context.coint_data = {}
    context.coint_pairs = {}
    context.spreads = {}
    context.zscores = {}
    context.real_yields = {}
    context.real_yield_keys = []
    context.pair_status = {}
    context.pair_weights = {}
    
    context.lookback = 20 # used for regression
    context.z_window = 20 # used for zscore calculation, must be <= lookback
    
    context.spread = np.ndarray((context.universe_size, 0))

    # Create our dynamic stock selector.
    algo.attach_pipeline(make_pipeline(), 'pipeline')
    algo.schedule_function(choose_pairs, date_rules.every_day(), time_rules.market_open(hours=0, minutes=1))
    
#empty all data structures
def empty_data(context):
    context.coint_data = {}
    context.coint_pairs = {}
    context.spreads = {}
    context.zscores = {}
    context.real_yields = {}
    context.real_yield_keys = []
    context.top_yield_pairs = []
    context.pair_weights = {}
   
#calculate total commission cost of a stock given betsize
def get_commission(data, stock, bet_size):
    price = data.current(stock, 'price')
    num_shares = bet_size/price
    return (COMMISSION*num_shares)

#return correlation and cointegration pvalue
def get_corr_coint(data, s1, s2, length):
    s1_price = data.history(s1, "price", length, '1d')
    s2_price = data.history(s2, "price", length, '1d')
    score, pvalue, _ = sm.coint(s1_price, s2_price)
    correlation = s1_price.corr(s2_price)
    
    return correlation, pvalue

#return long and short moving avg
def get_mvg_averages(data, s1, s2, long_length, short_length):
    prices = data.history([s1, s2], "price", long_length, '1d')
    short_prices = prices.iloc[-short_length:]
    long_ma = np.mean(prices[s1] - prices[s2])
    short_ma = np.mean(short_prices[s1] - short_prices[s2])
    return long_ma, short_ma

#calculate std
def get_std(data, s1, s2, length):
    prices = data.history([s1, s2], "price", length, '1d')
    std = np.std(prices[s1] - prices[s2])
    return std

#Set status of pair
def set_pair_status(context, pair, top_weight, bottom_weight, currShort, currLong):
    context.pair_status[pair]['top_weight'] = top_weight 
    context.pair_status[pair]['bottom_weight'] = bottom_weight
    context.pair_status[pair]['currently_short'] = currShort
    context.pair_status[pair]['currently_long'] = currLong
   
#select pairs based on correlation, cointegration
#rank pairs by estimated real yield
#weight top pairs by correlation
def choose_pairs(context, data):
    empty_data(context)
    context.universe = pipeline_output('pairs')
    if (context.universe_size > len(context.universe)):
        context.universe_size = len(context.universe) - 1
    context.target_weights = pd.Series(index=context.universe.index, data=0.25)    
    counter=0
    while (counter<=context.universe_size):
        end=counter+context.increments
        for i in range (counter,end):
            for j in range (i+1, context.universe_size):
                s1 = context.universe.index[i]
                s2 = context.universe.index[j]
                populate_pairs(context, data, s1, s2)
        counter+=context.increments
    for pair in context.coint_pairs:
        long_ma, short_ma = get_mvg_averages(data, pair[0], pair[1], context.long_ma_length, context.short_ma_length)
        long_std = get_std(data, pair[0], pair[1], context.long_ma_length)
        #calculate spread and zscore
        context.spreads[pair] = (short_ma-long_ma)/long_ma
        context.zscores[pair] = (short_ma-long_ma)/long_std
    
        port_val = context.portfolio.portfolio_value
        top_commission = get_commission(data, s1, port_val*0.5/context.num_pairs)
        bottom_commission = get_commission(data, s2, port_val*0.5/context.num_pairs)
        pair_commission = top_commission + bottom_commission
        context.real_yields[pair] = {}
        #subtract total commission of pair from % of portfolio value to get real yield
        context.real_yields[pair]['yield'] = abs(context.spreads[pair]) * port_val-pair_commission
        context.real_yields[pair]['corr'] = context.coint_data[pair]['corr']
    
    #sort pairs from highest to lowest correlations
    context.real_yield_keys = sorted(context.real_yields, key=lambda kv: context.real_yields[kv]['corr'], reverse=True)
    
    #select top num_pairs pairs
    if (context.num_pairs > len(context.real_yield_keys)):
        context.num_pairs = len(context.real_yield_keys)
    for i in range(context.num_pairs):
        context.top_yield_pairs.append(context.real_yield_keys[i])
    
    #determine weights of each pair based on correlation
    total_corr = 0
    for pair in context.top_yield_pairs:
        total_corr += context.real_yields[pair]['corr']
    
    for pair in context.top_yield_pairs:
        context.pair_weights[pair] = context.real_yields[pair]['corr']/total_corr
    
    #print final data
    print(context.real_yields) #prints yield and correlation of every pair that passed 1st screen
    print(context.pair_weights) #prints weight of every pair in final list of top pairs
    
    for pair in context.top_yield_pairs:
        context.pair_status[pair] = {}
        context.pair_status[pair]['currently_short'] = False
        context.pair_status[pair]['currently_long'] = False

def populate_pairs(context, data, s1, s2):
    #get correlation cointegration values
    correlation, coint_pvalue = get_corr_coint(data, s1, s2, context.price_history_length)
    context.coint_data[(s1,s2)] = {"corr": correlation, "coint": coint_pvalue}
    if (coint_pvalue < context.coint_cutoff and correlation > context.corr_cutoff):
        context.coint_pairs[(s1,s2)] = context.coint_data[(s1,s2)]
            
#INCOMPLETE
def check_pair_status(context, data):
    
    for pair in context.top_yield_pairs:
        if context.zscores[pair] > context.entry_threshold and not context.pair_status[pair]['currently_short']:
            set_pair_status(context, pair, -0.5, 0.5, True, False)
        
        elif context.zscores[pair] < -context.entry_threshold and not context.pair_status[pair]['currently_long']:
            set_pair_status(context, pair, 0.5, -0.5, False, True)
            
        elif abs(context.zscores[pair]) < context.exit_threshold:
            set_pair_status(context, pair, 0, 0, False, False) 
        
def make_pipeline():
    """
    A function to create our dynamic stock selector (pipeline). Documentation
    on pipeline can be found here:
    https://www.quantopian.com/help#pipeline-title
    """

    # Base universe set to the QTradableStocksUS
    base_universe = QTradableStocksUS()

    # Factor of yesterday's close price.
    yesterday_close = USEquityPricing.close.latest

    pipe = Pipeline(
        columns={
            'close': yesterday_close,
        },
        screen=base_universe
    )
    return pipe


def before_trading_start(context, data):
    """
    Called every day before market open.
    """
    context.output = algo.pipeline_output('pipeline')

    # These are the securities that we are interested in trading each day.
    context.security_list = context.output.index


def rebalance(context, data):
    """
    Execute orders according to our schedule_function() timing.
    """
    pass


def record_vars(context, data):
    """
    Plot variables at the end of each day.
    """
    pass


def handle_data(context, data):
    """
    Called every minute.
    """
    
    pass
