"""Loads a tool's library (not its models) and reports its version. Runs inside that tool's env."""
import json
import sys

pin, pout = sys.argv[1], sys.argv[2]
tool = json.load(open(pin))["tool"]
try:
    if tool == "chatterbox":
        import chatterbox.tts  # noqa: F401
        from importlib.metadata import version
        v = version("chatterbox-tts")
    elif tool == "whisperx":
        import whisperx  # noqa: F401
        from importlib.metadata import version
        v = version("whisperx")
    elif tool == "manim":
        import manim
        v = manim.__version__
    elif tool == "sdxl":
        import diffusers
        v = "diffusers " + diffusers.__version__
    elif tool == "acestep":
        import acestep.inference  # noqa: F401
        from importlib.metadata import version
        v = "ACE-Step " + version("ace-step")
    else:
        raise ValueError(tool)
    if tool in ("chatterbox", "whisperx", "sdxl", "acestep"):
        import torch
        if torch.cuda.is_available():
            v += f" on {torch.cuda.get_device_name(0)}"
    json.dump({"version": v}, open(pout, "w"))
except Exception as e:
    json.dump({"error": f"{type(e).__name__}: {e}"}, open(pout, "w"))
