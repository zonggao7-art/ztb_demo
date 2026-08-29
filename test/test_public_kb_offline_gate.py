"""公共知识库代码先行优化的离线发布门禁。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from public_kb.config import Settings
from public_kb.contracts import ConfigurationContractError, MilvusCollectionContract
from public_kb.services.milvus_store import MilvusStoreManager


class SettingsOfflineGateTests(unittest.TestCase):
    def test_explicit_uri_has_priority_over_host_port(self) -> None:
        settings = Settings(
            milvus_uri="http://new-milvus:19531",
            milvus_host="old-milvus",
            milvus_port="19530",
        )
        self.assertEqual(settings.resolved_milvus_uri, "http://new-milvus:19531")

    def test_host_port_fallback_is_backward_compatible(self) -> None:
        settings = Settings(milvus_uri="", milvus_host="10.0.0.8", milvus_port="19530")
        self.assertEqual(settings.resolved_milvus_uri, "http://10.0.0.8:19530")

    def test_environment_overrides_are_parsed_without_network_access(self) -> None:
        with patch.dict(os.environ, {
            "MILVUS_URI": "http://offline.invalid:19531",
            "MILVUS_COLLECTION": "public_kb_hybrid_poc_env",
            "ENABLE_MILVUS_BM25": "true",
            "STRICT_HYBRID_VALIDATION": "yes",
            "MILVUS_TIMEOUT": "17",
        }, clear=False):
            settings = Settings()
        self.assertEqual(settings.milvus_uri, "http://offline.invalid:19531")
        self.assertEqual(settings.collection_name, "public_kb_hybrid_poc_env")
        self.assertTrue(settings.enable_bm25)
        self.assertTrue(settings.strict_hybrid_validation)
        self.assertEqual(settings.milvus_timeout, 17)

    def test_token_is_not_exposed_by_connection_error_message(self) -> None:
        settings = Settings(
            milvus_uri="http://offline.invalid:19530",
            milvus_token="do-not-log-this-token",
        )
        manager = object.__new__(MilvusStoreManager)
        manager._settings = settings
        manager._uri = settings.resolved_milvus_uri
        args = manager._build_connection_args()
        self.assertEqual(args["token"], "do-not-log-this-token")
        self.assertNotIn("do-not-log-this-token", settings.resolved_milvus_uri)


class ContractOfflineGateTests(unittest.TestCase):
    def test_target_contract_rejects_field_collision_before_database_access(self) -> None:
        with self.assertRaises(ConfigurationContractError):
            MilvusCollectionContract(text_field="vector").validate()

    def test_production_collection_is_not_an_experiment_collection(self) -> None:
        settings = Settings(collection_name="public_kb")
        self.assertFalse(
            settings.collection_name.startswith(settings.milvus_experiment_prefix)
        )


if __name__ == "__main__":
    unittest.main()
