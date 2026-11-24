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
    if cuda and vram < 10:
        pipe.enable_model_cpu_offload()
    elif cuda:
        pipe.to("cuda")
    pipe.set_progress_bar_config(disable=True)
    out = []
    for im in job["images"]:
        try:
            g = torch.Generator("cpu").manual_seed(int(im.get("seed", 7)))
            with torch.inference_mode():
                img = pipe(prompt=im["prompt"], negative_prompt=NEGATIVE, num_inference_steps=int(job.get("steps", 4)),
                           guidance_scale=0.0, width=int(job.get("width", 512)), height=int(job.get("height", 896)),
                           generator=g).images[0]
            img.save(im["out"])
            out.append({"out": im["out"], "ok": True})
        except Exception as e:
            out.append({"out": im["out"], "ok": False, "error": f"{type(e).__name__}: {e}"[:300]})
    json.dump({"images": out, "device": torch.cuda.get_device_name(0) if cuda else "cpu",
               "took": round(time.time() - t0, 1)}, open(pout, "w"))
except Exception as e:
    json.dump({"error": f"{type(e).__name__}: {e}"}, open(pout, "w"))
