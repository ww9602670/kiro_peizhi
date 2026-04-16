"""
E2E测试配置

定义E2E测试的超时、轮询、测试账号等配置。
"""


class E2EConfig:
    """E2E测试配置类"""
    
    # 超时配置（秒）
    LOGIN_TIMEOUT = 5
    BET_TIMEOUT = 30
    SETTLEMENT_TIMEOUT = 60
    WORKER_START_TIMEOUT = 10
    
    # 轮询配置
    POLL_INTERVAL = 0.5  # 轮询间隔（秒）
    MAX_POLL_ATTEMPTS = 60  # 最大轮询次数
    
    # 测试账号配置
    TEST_USERNAME = "admin"
    TEST_PASSWORD = "admin123"
    TEST_ACCOUNT_NAME = "testuser01"  # 使用现有账号
    TEST_ACCOUNT_PASSWORD = "test166"
    TEST_PLATFORM_TYPE = "JND28WEB"  # 使用真实平台
    
    # 测试数据配置
    TEST_USER_PREFIX = "e2e_test"
    TEST_ACCOUNT_PREFIX = "e2e_acc"
    TEST_STRATEGY_PREFIX = "e2e_strat"
    
    # 测试策略配置
    TEST_STRATEGY_TYPE = "flat"
    TEST_PLAY_CODE = "DX1"
    TEST_BASE_AMOUNT = 10.0
    
    # API配置
    API_BASE_URL = "http://localhost:8888"
