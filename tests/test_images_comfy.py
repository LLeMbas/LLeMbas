"""The ComfyUI client, against recorded shapes.

Every response body here was read off a real ComfyUI 0.27.0 while this was being
written, rather than reconstructed from documentation -- including the one that
matters most, `/history` answering with an empty object while the job is still
queued.
"""

from __future__ import annotations

import httpx
import pytest

from lembas.services.images import comfy

CONFIG = comfy.Config(base_url="http://comfy.test:8188", timeout=5.0)

HISTORY_DONE = {
    "p1": {
        "status": {"status_str": "success", "completed": True, "messages": []},
        "outputs": {
            "9": {"images": [{"filename": "LLeMbas_00001_.png", "subfolder": "", "type": "output"}]}
        },
    }
}


def _handler(routes):
    """Answer by path, and record what was asked."""

    def handle(request: httpx.Request) -> httpx.Response:
        for path, responder in routes.items():
            if request.url.path.startswith(path):
                return responder(request)
        return httpx.Response(404, json={"error": "no route"})

    return handle


# --- Submitting ----------------------------------------------------------------
async def test_a_workflow_is_queued_and_its_id_returned(mock_http):
    seen: dict = {}

    def prompt(request):
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"prompt_id": "p1", "number": 3, "node_errors": {}})

    mock_http(_handler({"/prompt": prompt}))

    assert await comfy.submit(CONFIG, {"3": {"class_type": "KSampler"}}) == "p1"
    assert "KSampler" in seen["body"]
    assert "client_id" in seen["body"], "ComfyUI keys its progress socket on this"


async def test_a_graph_error_names_the_node(mock_http):
    """ "Invalid prompt" against a twelve-node document says nothing. The
    commonest cause by far is a checkpoint that does not exist on that machine,
    and the node id is what points at it."""
    mock_http(
        _handler(
            {
                "/prompt": lambda r: httpx.Response(
                    400,
                    json={
                        "error": {"type": "prompt_outputs_failed_validation"},
                        "node_errors": {
                            "4": {"errors": [{"message": "value not in list: ckpt_name"}]}
                        },
                    },
                )
            }
        )
    )

    with pytest.raises(comfy.ComfyError) as caught:
        await comfy.submit(CONFIG, {})
    assert "node 4" in caught.value.message
    assert "ckpt_name" in caught.value.message


async def test_an_accepted_workflow_with_no_id_is_refused(mock_http):
    mock_http(_handler({"/prompt": lambda r: httpx.Response(200, json={"number": 1})}))
    with pytest.raises(comfy.ComfyError):
        await comfy.submit(CONFIG, {})


async def test_an_unreachable_comfyui_says_so(mock_http):
    def refuse(request):
        raise httpx.ConnectError("nope", request=request)

    mock_http(_handler({"/prompt": refuse}))
    with pytest.raises(comfy.ComfyError) as caught:
        await comfy.submit(CONFIG, {})
    assert "Could not reach ComfyUI" in caught.value.message


# --- Waiting -------------------------------------------------------------------
async def test_an_empty_history_means_not_yet(mock_http, monkeypatch):
    """`/history/{id}` is empty while the job is queued and gains the whole
    record when it ends -- so empty is "not yet", not "nothing". Reading it the
    other way makes every generation fail the instant it is submitted."""
    monkeypatch.setattr(comfy, "POLL_INTERVAL", 0.01)
    calls = {"n": 0}

    def history(request):
        calls["n"] += 1
        return httpx.Response(200, json=HISTORY_DONE if calls["n"] >= 3 else {})

    mock_http(_handler({"/history": history}))

    refs = await comfy.await_images(CONFIG, "p1")
    assert calls["n"] == 3, "it kept asking"
    assert [ref.filename for ref in refs] == ["LLeMbas_00001_.png"]


async def test_waiting_gives_up_eventually(mock_http, monkeypatch):
    monkeypatch.setattr(comfy, "POLL_INTERVAL", 0.01)
    mock_http(_handler({"/history": lambda r: httpx.Response(200, json={})}))

    with pytest.raises(comfy.ComfyError) as caught:
        await comfy.await_images(comfy.Config(base_url=CONFIG.base_url, timeout=0.05), "p1")
    assert "did not finish" in caught.value.message


async def test_a_failed_workflow_is_not_an_empty_one(mock_http, monkeypatch):
    monkeypatch.setattr(comfy, "POLL_INTERVAL", 0.01)
    mock_http(
        _handler(
            {
                "/history": lambda r: httpx.Response(
                    200,
                    json={
                        "p1": {"status": {"status_str": "error", "completed": True}, "outputs": {}}
                    },
                )
            }
        )
    )
    with pytest.raises(comfy.ComfyError):
        await comfy.await_images(CONFIG, "p1")


async def test_images_are_read_from_every_node(mock_http, monkeypatch):
    """A template is somebody else's document and may save from a node called
    anything, or from two of them. Looking for `SaveImage` by name works on the
    shipped workflow and on nothing else."""
    monkeypatch.setattr(comfy, "POLL_INTERVAL", 0.01)
    mock_http(
        _handler(
            {
                "/history": lambda r: httpx.Response(
                    200,
                    json={
                        "p1": {
                            "status": {"status_str": "success", "completed": True},
                            "outputs": {
                                "12": {
                                    "images": [
                                        {"filename": "a.png", "subfolder": "s", "type": "output"}
                                    ]
                                },
                                "40": {"images": [{"filename": "b.png"}]},
                            },
                        }
                    },
                )
            }
        )
    )
    refs = await comfy.await_images(CONFIG, "p1")
    assert [r.filename for r in refs] == ["a.png", "b.png"]
    assert refs[0].subfolder == "s"


# --- Fetching ------------------------------------------------------------------
async def test_the_image_comes_back_as_bytes(mock_http):
    seen: dict = {}

    def view(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n" + b"x" * 100)

    mock_http(_handler({"/view": view}))

    payload = await comfy.fetch_image(CONFIG, comfy.Ref("a.png", "sub", "output"))
    assert payload.startswith(b"\x89PNG")
    assert seen["params"] == {"filename": "a.png", "subfolder": "sub", "type": "output"}


async def test_an_oversized_image_is_refused(mock_http, monkeypatch):
    """The one place an external service hands back raw bytes that are written
    to disk. Neither `audio.speak` nor `openai_client` has a cap to copy."""
    monkeypatch.setattr(comfy, "MAX_IMAGE_BYTES", 64)
    mock_http(_handler({"/view": lambda r: httpx.Response(200, content=b"x" * 4096)}))

    with pytest.raises(comfy.ComfyError):
        await comfy.fetch_image(CONFIG, comfy.Ref("a.png"))


async def test_an_empty_file_is_refused(mock_http):
    mock_http(_handler({"/view": lambda r: httpx.Response(200, content=b"")}))
    with pytest.raises(comfy.ComfyError):
        await comfy.fetch_image(CONFIG, comfy.Ref("a.png"))


# --- Freeing -------------------------------------------------------------------
async def test_freeing_never_raises(mock_http):
    """It runs on the way out of a generation that has already produced its
    image. Failing the whole tool because a memory hint was refused would be
    turning a tidy-up into an error."""

    def boom(request):
        raise httpx.ConnectError("gone", request=request)

    mock_http(_handler({"/free": boom}))
    await comfy.free(CONFIG)  # must not raise


async def test_freeing_asks_for_both(mock_http):
    seen: dict = {}

    def free(request):
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={})

    mock_http(_handler({"/free": free}))
    await comfy.free(CONFIG)
    assert "unload_models" in seen["body"] and "free_memory" in seen["body"]


# --- Discovery -----------------------------------------------------------------
async def test_discovery_reads_the_option_lists(mock_http):
    """The shape is `{node: {input: {required: {field: [[...values], {meta}]}}}}`
    -- a list whose first element is the options."""
    mock_http(
        _handler(
            {
                "/object_info/CheckpointLoaderSimple": lambda r: httpx.Response(
                    200,
                    json={
                        "CheckpointLoaderSimple": {
                            "input": {
                                "required": {"ckpt_name": [["a.safetensors", "b.safetensors"]]}
                            }
                        }
                    },
                ),
                "/object_info/KSampler": lambda r: httpx.Response(
                    200,
                    json={
                        "KSampler": {
                            "input": {
                                "required": {
                                    "sampler_name": [["euler", "dpmpp_2m"]],
                                    "scheduler": [["normal", "karras"]],
                                }
                            }
                        }
                    },
                ),
            }
        )
    )

    checkpoints, samplers, schedulers = await comfy.discover(CONFIG)
    assert checkpoints == ["a.safetensors", "b.safetensors"]
    assert samplers == ["euler", "dpmpp_2m"]
    assert schedulers == ["normal", "karras"]


async def test_a_custom_node_pack_that_changes_the_shape_is_survived(mock_http):
    """It is somebody else's schema. An empty list is a page that says "none
    found"; an exception is a page that says nothing at all."""
    mock_http(
        _handler(
            {
                "/object_info": lambda r: httpx.Response(200, json={"Whatever": {"input": {}}}),
            }
        )
    )
    assert await comfy.discover(CONFIG) == ([], [], [])


# --- Failing ---------------------------------------------------------------
# ComfyUI sets `completed=e.success`, so a run that *failed* is `completed:
# false` for ever. Waiting on that flag means every out-of-memory, every
# cancelled job and every broken node hangs the reply for the whole timeout and
# then reports a timeout -- when ComfyUI knew what was wrong within a second and
# had written it down. Every shape below was read off a real ComfyUI 0.27.0 by
# causing the failure rather than by imagining it.
def _failed(event, payload):
    return {
        "p1": {
            "status": {
                "status_str": "error",
                "completed": False,
                "messages": [
                    ["execution_start", {"prompt_id": "p1"}],
                    [event, payload],
                ],
            },
            "outputs": {},
        }
    }


def _history(record):
    return _handler({"/history": lambda r: httpx.Response(200, json=record)})


async def test_a_failure_is_noticed_at_once_rather_than_at_the_timeout(mock_http, monkeypatch):
    monkeypatch.setattr(comfy, "POLL_INTERVAL", 0.01)
    mock_http(_history(_failed("execution_error", {"exception_message": "boom"})))

    # A timeout long enough that waiting for it would hang the test.
    slow = comfy.Config(base_url=CONFIG.base_url, timeout=3600.0)
    with pytest.raises(comfy.ComfyError) as caught:
        await comfy.await_images(slow, "p1")
    assert "did not finish within" not in caught.value.message


async def test_running_out_of_memory_is_its_own_kind(mock_http, monkeypatch):
    """It is the one failure with an obvious next move, and the tool tells the
    model to make it."""
    monkeypatch.setattr(comfy, "POLL_INTERVAL", 0.01)
    mock_http(
        _history(
            _failed(
                "execution_error",
                {
                    "node_type": "KSampler",
                    "exception_type": "torch.OutOfMemoryError",
                    "exception_message": (
                        "CUDA out of memory. Tried to allocate 5.62 GiB. GPU 0 has a total "
                        "capacity of 15.92 GiB of which 108.00 MiB is free.\n"
                        "If reserved but unallocated memory is large try setting "
                        "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True"
                    ),
                },
            )
        )
    )

    with pytest.raises(comfy.OutOfMemory) as caught:
        await comfy.await_images(CONFIG, "p1")
    assert "video memory" in caught.value.message
    assert "KSampler" in caught.value.message, "which node ran out"
    assert "PYTORCH_CUDA_ALLOC_CONF" not in caught.value.message, (
        "allocator advice is addressed to whoever runs the box, not to a model"
    )


async def test_being_cancelled_is_not_a_fault(mock_http, monkeypatch):
    """Retrying a cancelled job is reasonable; "the workflow failed" would be
    describing somebody's decision as a breakage."""
    monkeypatch.setattr(comfy, "POLL_INTERVAL", 0.01)
    mock_http(_history(_failed("execution_interrupted", {"node_type": "KSampler"})))

    with pytest.raises(comfy.Interrupted) as caught:
        await comfy.await_images(CONFIG, "p1")
    assert "cancelled" in caught.value.message


async def test_a_node_that_raised_says_which_and_why(mock_http, monkeypatch):
    monkeypatch.setattr(comfy, "POLL_INTERVAL", 0.01)
    mock_http(
        _history(
            _failed(
                "execution_error",
                {
                    "node_type": "VAEDecode",
                    "exception_type": "ValueError",
                    "exception_message": "given tensor has the wrong shape",
                },
            )
        )
    )

    with pytest.raises(comfy.ComfyError) as caught:
        await comfy.await_images(CONFIG, "p1")
    assert "VAEDecode" in caught.value.message
    assert "wrong shape" in caught.value.message
    assert not isinstance(caught.value, comfy.OutOfMemory)


async def test_a_failure_with_nothing_recorded_still_says_something(mock_http, monkeypatch):
    monkeypatch.setattr(comfy, "POLL_INTERVAL", 0.01)
    mock_http(
        _history({"p1": {"status": {"status_str": "error", "completed": False}, "outputs": {}}})
    )

    with pytest.raises(comfy.ComfyError) as caught:
        await comfy.await_images(CONFIG, "p1")
    assert "could not finish" in caught.value.message


async def test_a_record_without_a_status_is_still_not_yet(mock_http, monkeypatch):
    """The terminal condition is the *status*, not the key. A record ComfyUI is
    still assembling must not be read as a silent failure."""
    monkeypatch.setattr(comfy, "POLL_INTERVAL", 0.01)
    mock_http(_history({"p1": {"outputs": {}}}))

    with pytest.raises(comfy.ComfyError) as caught:
        await comfy.await_images(comfy.Config(base_url=CONFIG.base_url, timeout=0.05), "p1")
    assert "did not finish within" in caught.value.message
