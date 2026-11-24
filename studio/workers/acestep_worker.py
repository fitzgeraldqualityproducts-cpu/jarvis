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

try:
    import torch
    from acestep.handler import AceStepHandler
    from acestep.inference import GenerationConfig, GenerationParams, generate_music
    t0 = time.time()
    cuda = torch.cuda.is_available()
    vram = torch.cuda.get_device_properties(0).total_memory / 2 ** 30 if cuda else 0
    small = vram < 12
    dit = AceStepHandler()
    msg, ok = dit.initialize_service(project_root=os.environ.get("JV_ACESTEP_SRC") or os.getcwd(),
                                     config_path="acestep-v15-turbo", device="cuda" if cuda else "cpu",
                                     offload_to_cpu=small, offload_dit_to_cpu=small,
                                     quantization="int8_weight_only" if small else None)
    if not ok:
        raise RuntimeError(f"ACE-Step didn't start: {msg}"[:300])
    params = GenerationParams(caption=job["caption"], lyrics="[Instrumental]", instrumental=True,
                              duration=float(job["duration"]), bpm=job.get("bpm"), seed=int(job.get("seed", 7)),
                              thinking=False, use_cot_metas=False, use_cot_caption=False, use_cot_language=False)
    config = GenerationConfig(batch_size=1, use_random_seed=False, seeds=[int(job.get("seed", 7))], audio_format="wav")
    with tempfile.TemporaryDirectory(prefix="jv-ace-") as tmp:
        res = generate_music(dit, None, params, config, save_dir=tmp)
        if not res.success or not res.audios:
            raise RuntimeError(f"ACE-Step made no music: {res.error or res.status_message}"[:300])
        shutil.copy(res.audios[0]["path"], job["out"])
    json.dump({"path": job["out"], "seconds": float(job["duration"]),
               "device": torch.cuda.get_device_name(0) if cuda else "cpu", "took": round(time.time() - t0, 1)},
              open(pout, "w"))
except Exception as e:
    json.dump({"error": f"{type(e).__name__}: {e}"}, open(pout, "w"))
