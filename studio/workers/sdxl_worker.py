"""AI illustrations with SDXL-Turbo (runs inside tools/sdxl). In: {"images": [{"prompt", "out", "seed"}],
"width", "height", "steps"}. Out: {"images": [{"out", "ok", "error"?}], "device", "took"}.

SDXL-Turbo (Stability AI Community License) makes a picture in 1-4 steps. On a 6 GB card the model is
moved to the graphics card one part at a time (CPU offload), and the fp16-safe VAE (madebyollin, MIT) is
used so half precision never gives black images. Pictures are stylised illustrations with no text: Jarvis
draws the numbers and words itself, on top."""
import json
import sys
import time

pin, pout = sys.argv[1], sys.argv[2]
job = json.load(open(pin))
NEGATIVE = "text, letters, words, numbers, watermark, logo, signature, caption, blurry, people, face, hands"

try:
    import torch
    from diffusers import AutoencoderKL, AutoPipelineForText2Image
    t0 = time.time()
    cuda = torch.cuda.is_available()
    dtype = torch.float16 if cuda else torch.float32
    vae = AutoencoderKL.from_pretrained("madebyollin/sdxl-vae-fp16-fix", torch_dtype=dtype)
    pipe = AutoPipelineForText2Image.from_pretrained("stabilityai/sdxl-turbo", vae=vae, torch_dtype=dtype,
                                                     variant="fp16" if cuda else None)
    vram = torch.cuda.get_device_properties(0).total_memory / 2 ** 30 if cuda else 0
    mode = "gpu"
    if cuda and vram < 8:
        # 6 GB cards (with the desktop holding ~1 GB): move one layer at a time to the card. Slower than whole-
        # model offload, but SDXL's 2.6 GB UNet never has to fit at once.
        pipe.enable_sequential_cpu_offload()
        mode = "sequential offload"
    elif cuda and vram < 12:
        pipe.enable_model_cpu_offload()
        mode = "model offload"
    elif cuda:
        pipe.to("cuda")
    pipe.enable_vae_slicing()
    pipe.enable_vae_tiling()
    pipe.set_progress_bar_config(disable=True)
    out = []
    for im in job["images"]:
        err = None
        for attempt in range(2):
            try:
                g = torch.Generator("cpu").manual_seed(int(im.get("seed", 7)))
                with torch.inference_mode():
                    img = pipe(prompt=im["prompt"], negative_prompt=NEGATIVE,
                               num_inference_steps=int(job.get("steps", 4)), guidance_scale=0.0,
                               width=int(job.get("width", 512)), height=int(job.get("height", 896)),
                               generator=g).images[0]
                img.save(im["out"])
                err = None
                break
            except torch.cuda.OutOfMemoryError as e:     # free what's cached and try that picture once more
                err = f"OutOfMemoryError: {e}"
                if cuda:
                    torch.cuda.empty_cache()
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                break
        if cuda:
            torch.cuda.empty_cache()
        out.append({"out": im["out"], "ok": err is None, **({"error": err[:300]} if err else {})})
    json.dump({"images": out, "device": (torch.cuda.get_device_name(0) + f" ({mode})") if cuda else "cpu",
               "took": round(time.time() - t0, 1)}, open(pout, "w"))
except Exception as e:
    json.dump({"error": f"{type(e).__name__}: {e}"}, open(pout, "w"))
