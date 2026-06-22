import httpx
import pytest
import respx
from novel2media.clients.comfyui import ComfyUIClient

BASE = "http://comfy.local:8188"


@respx.mock
def test_generate_images_returns_paths(tmp_path):
    prompt_id = "abc123"
    respx.post(f"{BASE}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": prompt_id}))
    image_bytes = b"FAKEPNG"
    respx.get(f"{BASE}/history/{prompt_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                prompt_id: {
                    "outputs": {
                        "9": {"images": [{"filename": "ComfyUI_00001_.png", "subfolder": "", "type": "output"}]}
                    }
                }
            },
        )
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


@respx.mock
def test_upload_image_returns_name(tmp_path):
    img = tmp_path / "face.png"
    img.write_bytes(b"fake_png_bytes")
    respx.post(f"{BASE}/upload/image").mock(
        return_value=httpx.Response(200, json={"name": "face.png", "subfolder": ""})
    )
    client = ComfyUIClient(base_url=BASE)
    name = client.upload_image(img)
    assert name == "face.png"


@respx.mock
def test_upload_image_with_subfolder(tmp_path):
    img = tmp_path / "portrait.png"
    img.write_bytes(b"fake_png_bytes")
    respx.post(f"{BASE}/upload/image").mock(
        return_value=httpx.Response(200, json={"name": "portrait.png", "subfolder": "characters"})
    )
    client = ComfyUIClient(base_url=BASE)
    name = client.upload_image(img, subfolder="characters")
    assert name == "portrait.png"


@respx.mock
def test_submit_returns_prompt_id():
    respx.post(f"{BASE}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "pid-1"}))
    client = ComfyUIClient(base_url=BASE)
    assert client.submit({"any": "workflow"}) == "pid-1"


@respx.mock
def test_fetch_result_none_when_not_in_history():
    """任务还没进 history → fetch_result 返回 None（让上层继续轮询）。"""
    respx.get(f"{BASE}/history/pid-1").mock(return_value=httpx.Response(200, json={}))
    client = ComfyUIClient(base_url=BASE)
    assert client.fetch_result("pid-1") is None


@respx.mock
def test_fetch_result_none_when_no_output_images():
    """已进 history 但还没产出 output 图 → None。"""
    respx.get(f"{BASE}/history/pid-1").mock(
        return_value=httpx.Response(200, json={"pid-1": {"outputs": {}}})
    )
    client = ComfyUIClient(base_url=BASE)
    assert client.fetch_result("pid-1") is None


@respx.mock
def test_fetch_result_returns_output_images_only():
    """只收 type=output 的图，跳过 temp/预览。"""
    respx.get(f"{BASE}/history/pid-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "pid-1": {
                    "outputs": {
                        "8": {
                            "images": [
                                {"filename": "temp_.png", "subfolder": "", "type": "temp"},
                                {"filename": "out_.png", "subfolder": "", "type": "output"},
                            ]
                        }
                    }
                }
            },
        )
    )
    client = ComfyUIClient(base_url=BASE)
    images = client.fetch_result("pid-1")
    assert images == [{"filename": "out_.png", "subfolder": "", "type": "output"}]


@respx.mock
def test_fetch_result_raises_on_task_error():
    """ComfyUI 任务执行出错 → 抛错暴露，不静默返回空。"""
    respx.get(f"{BASE}/history/pid-1").mock(
        return_value=httpx.Response(
            200, json={"pid-1": {"status": {"status_str": "error"}, "outputs": {}}}
        )
    )
    client = ComfyUIClient(base_url=BASE)
    with pytest.raises(RuntimeError, match="任务执行出错"):
        client.fetch_result("pid-1")


@respx.mock
def test_wait_for_output_times_out():
    """轮询超时抛 TimeoutError，不无限等待。"""
    respx.get(f"{BASE}/history/pid-1").mock(return_value=httpx.Response(200, json={}))
    client = ComfyUIClient(base_url=BASE, poll_interval=0)
    with pytest.raises(TimeoutError, match="任务超时"):
        client._wait_for_output("pid-1", timeout=0.01)

