#!/usr/bin/env python3
"""Download camera-C videos for selected dishes and extract frame 10 -> view-c jpg.

Idempotent: skips dishes that already have data/images/<dish>_view_c.jpg.
Pulls camera_C.h264 from the public Nutrition5K GCS bucket over HTTPS (no auth,
free), extracts the 10th frame (n=9, matches the existing canonical images,
MAD~1.9), writes the jpg, deletes the video. Failures are logged so they can be
replaced. Run:  python3 tools/fetch_extract_images.py [selected_file]
"""
import os,sys,json,subprocess,tempfile,urllib.request,shutil
from concurrent.futures import ThreadPoolExecutor,as_completed
HERE=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); os.chdir(HERE)
SEL=sys.argv[1] if len(sys.argv)>1 else "data/selected_500.txt"
URL="https://storage.googleapis.com/nutrition5k_dataset/nutrition5k_dataset/imagery/side_angles/{d}/camera_C.h264"
IMG="data/images/{d}_view_c.jpg"; WORKERS=8; TMP=tempfile.mkdtemp(prefix="n5k_")
sel=[l.strip() for l in open(SEL) if l.strip()]
todo=[d for d in sel if not os.path.exists(IMG.format(d=d))]
print(f"{len(sel)} selected, {len(sel)-len(todo)} present, {len(todo)} to fetch",flush=True)
done=[0]; fails=[]
def fetch(d):
    vid=os.path.join(TMP,f"{d}.h264"); out=IMG.format(d=d)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(URL.format(d=d),timeout=120) as r, open(vid,"wb") as fh:
                shutil.copyfileobj(r,fh)
            subprocess.run(["ffmpeg","-loglevel","error","-i",vid,"-vf","select=eq(n\\,9)",
                            "-vframes","1",out,"-y"],check=True,timeout=120)
            if os.path.getsize(out)>1000: return True
            raise RuntimeError("empty frame")
        except Exception as e:
            err=str(e)[:80]
        finally:
            if os.path.exists(vid): os.remove(vid)
    fails.append(d); return False
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs={ex.submit(fetch,d):d for d in todo}
    for fu in as_completed(futs):
        done[0]+=1
        if done[0]%25==0 or done[0]==len(todo): print(f"  {done[0]}/{len(todo)} ({len(fails)} failed)",flush=True)
shutil.rmtree(TMP,ignore_errors=True)
if fails: open("data/image_fetch_failures.txt","w").write("\n".join(fails)+"\n")
have=sum(1 for d in sel if os.path.exists(IMG.format(d=d)))
print(f"DONE. images present for {have}/{len(sel)} selected; {len(fails)} failures"+(" (see data/image_fetch_failures.txt)" if fails else ""),flush=True)
