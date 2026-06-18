import pytest
from novel2media.llm import get_llm


def test_get_llm_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ARK_API_KEY"):
        get_llm()


def test_get_llm_returns_instance(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    llm = get_llm(temperature=0.5)
    assert llm is not None
    assert llm.temperature == 0.5


def test_get_llm_custom_model(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.setenv("ARK_MODEL", "my-custom-model")
    llm = get_llm()
    assert llm.model_name == "my-custom-model"
