"""全局应用配置（网页版）"""
import os


class AppConfig:
    # ========== 路径配置 ==========
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    STOCK_DATA_DIR = os.path.join(BASE_DIR, "stock_data")
    DATA_DIR = os.path.join(BASE_DIR, "data")
    LOGS_DIR = os.path.join(BASE_DIR, "logs")
    WEB_DIR = os.path.join(BASE_DIR, "web")

    # 持久化文件路径
    ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")
    OPTIMAL_CACHE_FILE = os.path.join(DATA_DIR, "optimal_cache.json")
    CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
    GLOSSARY_FILE = os.path.join(DATA_DIR, "glossary.json")
    BOX_STRATEGIES_FILE = os.path.join(DATA_DIR, "box_strategies.json")
    BACKTEST_CACHE_FILE = os.path.join(DATA_DIR, "backtest_cache.json")
    TRADE_HISTORY_FILE = os.path.join(DATA_DIR, "trade_history.json")

    # ========== 交易参数 ==========
    COMMISSION_RATE = 0.0003       # 佣金费率
    MIN_COMMISSION = 5.0           # 最低佣金
    SLIPPAGE_BUY = 0.001           # 买入滑点
    SLIPPAGE_SELL = -0.001         # 卖出滑点
    LOT_SIZE = 100                 # A股每手100股
    INITIAL_CASH = 100000.0        # 初始资金

    # ========== 技术指标默认参数 ==========
    DEFAULT_MACD_FAST = 12
    DEFAULT_MACD_SLOW = 26
    DEFAULT_MACD_SIGNAL = 9
    DEFAULT_RSI_PERIOD = 14
    DEFAULT_RSI_LOW = 30
    DEFAULT_RSI_HIGH = 70
    DEFAULT_KDJ_N = 9
    DEFAULT_KDJ_M1 = 3
    DEFAULT_KDJ_M2 = 3
    DEFAULT_BOLL_PERIOD = 20
    DEFAULT_BOLL_STD = 2.0
    DEFAULT_VOL_RATIO = 1.2
    DEFAULT_MIN_AMOUNT = 50000000  # 最低成交额5000万

    # ========== 服务配置 ==========
    HOST = "127.0.0.1"
    PORT = 8666

    @classmethod
    def ensure_dirs(cls):
        for d in [cls.STOCK_DATA_DIR, cls.DATA_DIR, cls.LOGS_DIR]:
            os.makedirs(d, exist_ok=True)


AppConfig.ensure_dirs()
