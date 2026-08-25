"""诊断脚本共用工具 — MySQL 连接。

test/ 下仍活跃的诊断脚本（db_explorer / create_fulltext_indexes）共用；
已归档至 test/legacy/ 的历史脚本不引用本模块。
各脚本保留自己的 DB_CONFIG（超时参数等按用途不同），仅收敛连接构造逻辑。
"""

import pymysql


def get_connection(database=None, *, db_config):
    """基于调用方提供的 DB_CONFIG 副本创建 MySQL 连接。

    Args:
        database: 目标库名；None 表示不指定。
        db_config: 脚本自身的连接配置 dict（如 host/user/password/timeout）。
    """
    config = dict(db_config)
    if database:
        config["database"] = database
    return pymysql.connect(**config)
