import time
import os
try:
    import psutil  # optional dependency – only used if present
except ImportError:  # psutil is not mandatory for ExecutionTime to work
    psutil = None

import torch

import execution
import server
# import model_management


class ExecutionTime:
    CATEGORY = "TyDev-Utils/Debug"

    @classmethod
    def INPUT_TYPES(s):
        return {"required": {}}

    RETURN_TYPES = ()
    RETURN_NAMES = ()
    FUNCTION = "process"

    def process(self):
        return ()


CURRENT_START_EXECUTION_DATA = None


# def get_free_vram():
#     dev = model_management.get_torch_device()
#     if hasattr(dev, 'type') and (dev.type == 'cpu' or dev.type == 'mps'):
#         return 0
#     else:
#         return model_management.get_free_memory(dev)


def get_peak_memory():
    if not torch.cuda.is_available():
        return 0
    device = torch.device('cuda')
    return torch.cuda.max_memory_allocated(device)


def reset_peak_memory_record():
    if not torch.cuda.is_available():
        return
    device = torch.device('cuda')
    torch.cuda.reset_max_memory_allocated(device)


def _get_process():
    """Return a cached psutil.Process instance or None if psutil is missing."""
    if psutil is None:
        return None
    try:
        return psutil.Process(os.getpid())
    except Exception:
        return None


def get_cpu_time():
    """Total user+system CPU time consumed by the current process (seconds)."""
    proc = _get_process()
    if proc is None:
        return 0.0
    try:
        t = proc.cpu_times()
        return (t.user + t.system)
    except Exception:
        return 0.0


def get_ram_usage():
    """Resident set size in bytes for the current process."""
    proc = _get_process()
    if proc is None:
        return 0
    try:
        return proc.memory_info().rss
    except Exception:
        return 0


def handle_execute(class_type, last_node_id, prompt_id, server, unique_id):
    if not CURRENT_START_EXECUTION_DATA:
        return
    start_time = CURRENT_START_EXECUTION_DATA['nodes_start_perf_time'].get(unique_id)
    start_vram = CURRENT_START_EXECUTION_DATA['nodes_start_vram'].get(unique_id)
    start_cpu = CURRENT_START_EXECUTION_DATA['nodes_start_cpu'].get(unique_id, 0.0) if CURRENT_START_EXECUTION_DATA else 0.0
    start_ram = CURRENT_START_EXECUTION_DATA['nodes_start_ram'].get(unique_id, 0) if CURRENT_START_EXECUTION_DATA else 0
    if start_time:
        end_time = time.perf_counter()
        execution_time = end_time - start_time

        end_vram = get_peak_memory()
        vram_used = end_vram - start_vram
        print(f"end_vram - start_vram: {end_vram} - {start_vram} = {vram_used}")

        end_cpu = get_cpu_time()
        cpu_time_used = end_cpu - start_cpu  # seconds

        end_ram = get_ram_usage()
        ram_used = end_ram - start_ram  # bytes

        # Prepare data payload
        payload = {
            "node": unique_id,
            "prompt_id": prompt_id,
            "execution_time": int(execution_time * 1000),  # ms
            "vram_used": vram_used,
            "cpu_time_used": int(cpu_time_used * 1000),  # ms
            "ram_used": ram_used,
        }
        if server.client_id is not None and last_node_id != server.last_node_id:
            server.send_sync("TyDev-Utils.ExecutionTime.executed", payload, server.client_id)

        # Console output
        print(f"#{unique_id} [{class_type}]: {execution_time:.2f}s | CPU {cpu_time_used:.3f}s | RAM {ram_used / (1024**2):.2f}MB | VRAM {vram_used / (1024**2):.2f}MB")


try:
    origin_execute = execution.execute


    def swizzle_execute(server, dynprompt, caches, current_item, extra_data, executed, prompt_id, execution_list,
                        pending_subgraph_results):
        unique_id = current_item
        class_type = dynprompt.get_node(unique_id)['class_type']
        last_node_id = server.last_node_id
        result = origin_execute(server, dynprompt, caches, current_item, extra_data, executed, prompt_id,
                                execution_list,
                                pending_subgraph_results)
        handle_execute(class_type, last_node_id, prompt_id, server, unique_id)
        return result


    execution.execute = swizzle_execute
except Exception as e:
    pass

# region: Deprecated
try:
    # The execute method in the old version of ComfyUI is now deprecated.
    origin_recursive_execute = execution.recursive_execute


    def swizzle_origin_recursive_execute(server, prompt, outputs, current_item, extra_data, executed, prompt_id,
                                         outputs_ui,
                                         object_storage):
        unique_id = current_item
        class_type = prompt[unique_id]['class_type']
        last_node_id = server.last_node_id
        result = origin_recursive_execute(server, prompt, outputs, current_item, extra_data, executed, prompt_id,
                                          outputs_ui,
                                          object_storage)
        handle_execute(class_type, last_node_id, prompt_id, server, unique_id)
        return result


    execution.recursive_execute = swizzle_origin_recursive_execute
except Exception as e:
    pass
# endregion

origin_func = server.PromptServer.send_sync


def swizzle_send_sync(self, event, data, sid=None):
    # print(f"swizzle_send_sync, event: {event}, data: {data}")
    global CURRENT_START_EXECUTION_DATA
    if event == "execution_start":
        CURRENT_START_EXECUTION_DATA = dict(
            start_perf_time=time.perf_counter(),
            start_cpu=get_cpu_time(),
            start_ram=get_ram_usage(),
            nodes_start_perf_time={},
            nodes_start_vram={},
            nodes_start_cpu={},
            nodes_start_ram={},
        )
        reset_peak_memory_record()  # reset global peak VRAM tracker

    origin_func(self, event=event, data=data, sid=sid)

    if event == "executing" and data and CURRENT_START_EXECUTION_DATA:
        if data.get("node") is None:
            if sid is not None:
                start_perf_time = CURRENT_START_EXECUTION_DATA.get('start_perf_time')
                new_data = data.copy()
                if start_perf_time is not None:
                    execution_time = time.perf_counter() - start_perf_time
                    new_data['execution_time'] = int(execution_time * 1000)
                origin_func(
                    self,
                    event="TyDev-Utils.ExecutionTime.execution_end",
                    data=new_data,
                    sid=sid
                )
        else:
            node_id = data.get("node")
            CURRENT_START_EXECUTION_DATA['nodes_start_perf_time'][node_id] = time.perf_counter()
            reset_peak_memory_record()
            CURRENT_START_EXECUTION_DATA['nodes_start_vram'][node_id] = get_peak_memory()
            CURRENT_START_EXECUTION_DATA['nodes_start_cpu'][node_id] = get_cpu_time()
            CURRENT_START_EXECUTION_DATA['nodes_start_ram'][node_id] = get_ram_usage()

    if event == "executing" and data and CURRENT_START_EXECUTION_DATA and data.get("node") is None:
        # total run finished – add overall metrics
        start_cpu_total = CURRENT_START_EXECUTION_DATA.get("start_cpu", 0.0)
        start_ram_total = CURRENT_START_EXECUTION_DATA.get("start_ram", 0)
        cpu_time_total = get_cpu_time() - start_cpu_total
        ram_total_used = get_ram_usage() - start_ram_total
        new_data = data.copy()
        new_data["cpu_time_used"] = int(cpu_time_total * 1000)
        new_data["ram_used"] = ram_total_used
        new_data["total_vram_used"] = get_peak_memory()  # global peak since reset
        # send patched payload
        origin_func(
            self,
            event="TyDev-Utils.ExecutionTime.execution_end",
            data=new_data,
            sid=sid,
        )
        return  # prevent duplicate send below


server.PromptServer.send_sync = swizzle_send_sync
