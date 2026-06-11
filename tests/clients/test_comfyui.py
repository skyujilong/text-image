import pytest
import respx
import httpx
from novel2media.clients.comfyui import ComfyUIClient

BASE = "http://comfy.local:8188"


@respx.mock
def test_generate_images_returns_paths(tmp_path):
    prompt_id = "abc123"
    respx.post(f"{BASE}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": prompt_id})
    )
    image_bytes = b"FAKEPNG"
    respx.get(f"{BASE}/history/{prompt_id}").mock(
        return_value=httpx.Response(200, json={
            prompt_id: {
                "outputs": {
                    "9": {"images": [{"filename": "ComfyUI_00001_.png", "subfolder": "", "type": "output"}]}
                }
            }
        })
    )
    respx.get(f"{BASE}/view").mock(return_value=httpx.Response(200, content=image_bytes))

    client = ComfyUIClient(base_url=BASE, timeout=10, poll_interval=0)
    paths = client.generate(
        workflow_prompt={"positive": "a cat", "negative": ""},
        output_dir=tmp_path,
        count=1,
    )
    assert len(paths) == 1
    assert paths[0].exists()
    assert paths[0].read_bytes() == image_bytes


@respx.mock
def test_generate_raises_on_prompt_failure():
    respx.post(f"{BASE}/prompt").mock(return_value=httpx.Response(500))
    client = ComfyUIClient(base_url=BASE, timeout=10, max_retries=1, backoff=0)
    with pytest.raises(RuntimeError, match="ComfyUI prompt 提交失败"):
        from pathlib import Path
        client.generate(workflow_prompt={}, output_dir=Path("/tmp"), count=1)
