#!/usr/bin/env python
#  -*- coding: utf-8 -*-
"""
交易日历处理
"""
__author__ = 'mayanqiong'

import os
from datetime import date
from typing import Union, List

import pandas as pd
import requests

from tqsdk.datetime import _cst_tz, _timestamp_nano_to_datetime

rest_days_df = None
chinese_holidays_range = None


def _init_chinese_rest_days(headers=None):
    global rest_days_df, chinese_holidays_range
    if rest_days_df is None:
        url = os.getenv("TQ_CHINESE_HOLIDAY_URL", "https://files.shinnytech.com/shinny_chinese_holiday.json")
        rsp = requests.get(url, timeout=30, headers=headers)
        chinese_holidays = rsp.json()
        _first_day = date(int(chinese_holidays[0].split('-')[0]), 1, 1)  # 首个日期所在年份的第一天
        _last_day = date(int(chinese_holidays[-1].split('-')[0]), 12, 31)  # 截止日期所在年份的最后一天
        chinese_holidays_range = (_first_day, _last_day)
        rest_days_df = pd.DataFrame(data={'date': pd.Series(pd.to_datetime(chinese_holidays, format='%Y-%m-%d'))})
        rest_days_df['trading_restdays'] = False  # 节假日为 False
    return chinese_holidays_range


def _get_trading_calendar(start_dt: date, end_dt: date, headers=None):
    """
    获取一段时间内，每天是否是交易日

    :return: DataFrame
        date  trading
    2019-12-05  True
    2019-12-06  True
    2019-12-07  False
    2019-12-08  False
    2019-12-09  True
    """
    _init_chinese_rest_days(headers=headers)
    df = pd.DataFrame()
    df['date'] = pd.Series(pd.date_range(start=start_dt, end=end_dt, freq="D"))
    df['trading'] = df['date'].dt.dayofweek.lt(5) & ~df['date'].isin(rest_days_df['date'])
    return df


class TqContCalendar(object):

    """
    主连日历
    df:
        date   trading  KQ.m@DCE.a  KQ.m@DCE.eg
    2019-12-06    True  DCE.a2005  DCE.eg2001
    2019-12-09    True  DCE.a2005  DCE.eg2001
    2019-12-10    True  DCE.a2005  DCE.eg2005
    2019-12-11    True  DCE.a2005  DCE.eg2005
    2019-12-12    True  DCE.a2005  DCE.eg2005
    """

    continuous = None

    def __init__(self, start_dt: date, end_dt: date, symbols: Union[List[str], None] = None, headers=None) -> None:
        """
        初始化主连日历表
        :param date start_dt: 开始交易日日期
        :param date end_dt: 结束交易日日期
        :param list[str] symbols: 主连合约列表
        :return:
        """
        self.df = _get_trading_calendar(start_dt=start_dt, end_dt=end_dt, headers=headers)
        self.df = self.df.loc[self.df.trading, ['date']]  # 只保留交易日
        self.df.reset_index(inplace=True, drop=True)
        self.df['_cst_date'] = self.df.date.dt.tz_localize(_cst_tz)  # 增加一列，将 date 转换为东八区时间, tqsdk 内部使用
        if TqContCalendar.continuous is None:
            rsp = requests.get(os.getenv("TQ_CONT_TABLE_URL", "https://files.shinnytech.com/continuous_table.json"), headers=headers)  # 下载历史主连合约信息
            rsp.raise_for_status()
            TqContCalendar.continuous = {f"KQ.m@{k}": v for k, v in rsp.json().items()}
        if symbols is not None:
            if not all([s in TqContCalendar.continuous.keys() for s in symbols]):
                raise Exception(f"参数错误，symbols={symbols} 中应该全部都是主连合约代码")
        symbols = TqContCalendar.continuous.keys() if symbols is None else symbols
        self.start_dt, self.end_dt = self.df.iloc[0]._cst_date, self.df.iloc[-1]._cst_date
        for s in symbols:
            self._ensure_cont_on_df(s)

    def _ensure_cont_on_df(self, cont):
        """将一个主连对应的标的填在 self.df 对应位置"""
        temp_df = pd.DataFrame(data=TqContCalendar.continuous[cont], columns=['date', 'underlying'])
        temp_df['date'] = pd.Series(pd.to_datetime(temp_df['date'], format='%Y%m%d'))
        temp_df['_cst_date'] = temp_df.date.dt.tz_localize(_cst_tz)  # 增加一列，将 date 转换为东八区时间
        merge_result = pd.merge(temp_df, self.df, sort=True, how="outer", on="_cst_date")
        merge_result.ffill(inplace=True)
        merge_result.fillna(value="", inplace=True)
        # 由于主连表没有承诺主力合约在交易日更新，所以在主连表中非交易日可能也会有主力合约，比如 DCE.v1401
        # 所以 merge_result 中可能会有 _cst_date 在交易日之外的日期，导致和 self.df 行数对不齐
        # 所以 merge_result 只保留 self.df 中的交易日
        s = merge_result.loc[merge_result['_cst_date'].isin(set(self.df['_cst_date'])), 'underlying']
        self.df[cont] = pd.Series(s.values)

    def _get_cont_underlying_on_date(self, trading_day: int):
        """返回某一交易日的全部主连"""
        # trading_day 为北京时间的某一天的 00:00:00 的时间戳
        # 所以不能直接和 date 列比较，考虑到时区问题，增加 _cst_date 列，先转换为东八区时间，再比较
        dt = _timestamp_nano_to_datetime(trading_day)
        df = self.df.loc[self.df._cst_date.ge(dt), :]
        return df.iloc[0:1]
