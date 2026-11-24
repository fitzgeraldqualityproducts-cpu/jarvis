"""Background music with ACE-Step 1.5 (runs inside tools/acestep). In: {"caption", "duration", "out", "seed",
"bpm"}. Out: {"path", "seconds", "device", "took"}.

Instrumental only (no vocals, no lyrics). On a card with 6-8 GB the 2B turbo model runs without its
language model, with INT8 weights and CPU offload, as ACE-Step's own guide says for that size. ACE-Step
(MIT) says its music may be used commercially."""
import json
import os
import shutil
import sys
import tempfile
import time

pin, pout = sys.argv[1], sys.argv[2]
job = json.load(open(pin))

def _vram():
    import torch
    return torch.cuda.get_device_properties(0).total_memory / 2 ** 30 if torch.cuda.is_available() else 0


def _lean_loading():
    """ACE-Step loads its models with transformers' from_pretrained and no memory options. Ask it to load
    the weights straight into place instead of building a second full copy first (same weights, same
    music, about half the peak memory)."""
    try:
        import accelerate  # noqa: F401  (transformers needs it for this option)
        import transformers
    except Exception:
        return
    for name in ("AutoModel", "AutoModelForCausalLM"):
        cls = getattr(transformers, name, None)
        if cls is None or getattr(cls, "_jv_lean", False):
            continue
        orig = cls.from_pretrained.__func__

        def lean(c, *a, _orig=orig, **k):
            k.setdefault("low_cpu_mem_usage", True)
            return _orig(c, *a, **k)
        cls.from_pretrained = classmethod(lean)
        cls._jv_lean = True


def _no_resources(e):
    """Windows error 1450: the computer ran out of memory it could reserve (RAM plus page file)."""
    t = str(e).lower()
    return "1450" in t or "insufficient system resources" in t or "paging file is too small" in t


def _old_card():
    """True on a graphics card older than the RTX 30 series (Turing and before: no bfloat16). There
    ACE-Step's float16 overflows into NaN, so it has to run in float32, as ACE-Step itself advises."""
    import torch
    return torch.cuda.is_available() and torch.cuda.get_device_capability(0)[0] < 8


def _bad_numbers(e):
    t = str(e)
    return "NaN" in t or "Inf latents" in t


def generate(device, small, quant=True):
    """One instrumental piece on this device. Returns the path ACE-Step wrote (inside a temp folder that the
    caller copies from) and the folder to clean up."""
    _lean_loading()
    from acestep.handler import AceStepHandler
    from acestep.inference import GenerationConfig, GenerationParams, generate_music
    dit = AceStepHandler()
    msg, ok = dit.initialize_service(project_root=os.environ.get("JV_ACESTEP_SRC") or os.getcwd(),
                                     config_path="acestep-v15-turbo", device=device,
                                     offload_to_cpu=small, offload_dit_to_cpu=small,
                                     quantization="int8_weight_only" if quant and small and device == "cuda" else None)
    if not ok:
        raise RuntimeError(f"ACE-Step didn't start: {msg}"[:300])
    params = GenerationParams(caption=job["caption"], lyrics="[Instrumental]", instrumental=True,
                              duration=float(job["duration"]), bpm=job.get("bpm"), seed=int(job.get("seed", 7)),
                              thinking=False, use_cot_metas=False, use_cot_caption=False, use_cot_language=False)
    config = GenerationConfig(batch_size=1, use_random_seed=False, seeds=[int(job.get("seed", 7))], audio_format="wav")
    tmp = tempfile.mkdtemp(prefix="jv-ace-")
    res = generate_music(dit, None, params, config, save_dir=tmp)
    if not res.success or not res.audios:
        raise RuntimeError(f"ACE-Step made no music: {res.error or res.status_message}"[:300])
    return res.audios[0]["path"], tmp


try:
    t0 = time.time()
    import torch
    cuda = torch.cuda.is_available()
    device, note = ("cuda" if cuda else "cpu"), ""
    if _old_card():
        os.environ["ACESTEP_DTYPE"] = "float32"   # read by ACE-Step when it loads; must be set before that
    try:
        try:
            path, tmp = generate(device, small=_vram() < 12)
        except Exception as e:
            if not (cuda and _bad_numbers(e)):
                raise
            torch.cuda.empty_cache()             # once more without the 8-bit weights, in full precision
            os.environ["ACESTEP_DTYPE"] = "float32"
            note = "made again in full precision after the first try came out silent"
            path, tmp = generate(device, small=True, quant=False)
    except Exception as e:
        if _no_resources(e):                     # the processor would need the same memory: don't retry
            raise RuntimeError("the computer ran out of memory loading the music model (RAM plus page file); "
                               "a bigger page file on D: would make room")
        if not (cuda and ("out of memory" in str(e).lower() or isinstance(e, torch.cuda.OutOfMemoryError))):
            raise
        torch.cuda.empty_cache()                 # the card is too full today: make it on the processor instead
        device, note = "cpu", "the graphics card ran out of memory, so it was made on the processor"
        path, tmp = generate("cpu", small=False)
    shutil.copy(path, job["out"])
    shutil.rmtree(tmp, ignore_errors=True)
    json.dump({"path": job["out"], "seconds": float(job["duration"]), "note": note,
               "device": torch.cuda.get_device_name(0) if device == "cuda" else "cpu", "took": round(time.time() - t0, 1)},
              open(pout, "w"))
except Exception as e:
    json.dump({"error": f"{type(e).__name__}: {e}"}, open(pout, "w"))
