"""CUDA device selection without an implicit fallback to host inference."""


def cuda_device_name(value: str) -> str:
    if value in ("auto", "cuda"):
        return "cuda"
    if isinstance(value, str) and value.startswith("cuda:"):
        index = value.removeprefix("cuda:")
        if index.isascii() and index.isdecimal():
            return f"cuda:{int(index)}"
    raise ValueError("model device must be cuda, cuda:<index>, or auto (CUDA required)")


def require_cuda(value: str = "cuda") -> str:
    device = cuda_device_name(value)
    try:
        import torch
    except ImportError as exc:
        raise ImportError("Install CUDA-enabled PyTorch; see SETUP.md") from exc
    if not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA unavailable: install GPU PyTorch and check the NVIDIA driver"
        )
    if ":" in device and int(device.split(":")[1]) >= torch.cuda.device_count():
        raise RuntimeError(f"CUDA device unavailable: {device}")
    return device
