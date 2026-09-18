from __future__ import annotations
import argparse, json, subprocess, sys
from datetime import datetime
from pathlib import Path
from tts_common import ROOT, analysis_python
from tts_settings import WEIGHT_TYPES

def run_profile(variant,text,language,reference,t3_type,s3_type):
    cmd=[sys.executable,str(ROOT/f"tts_{variant}.py"),"--t3-weight-type",t3_type,"--s3-weight-type",s3_type,"--reference",str(reference),"--analysis","none","--determinism-repeats","0",text]
    if language is not None: cmd.append(language)
    proc=subprocess.run(cmd,cwd=str(ROOT),stdout=subprocess.PIPE,text=True)
    if proc.returncode: raise SystemExit(proc.returncode)
    lines=[x.strip() for x in proc.stdout.splitlines() if x.strip()]
    if not lines: raise RuntimeError("launcher did not report output path")
    wav=Path(lines[-1]).resolve(); run_dir=wav.parent/f"{wav.stem}_log"
    if not wav.is_file() or not run_dir.is_dir(): raise RuntimeError(f"incomplete launcher result: {wav}")
    return run_dir,cmd

def main(argv):
    p=argparse.ArgumentParser(); p.add_argument("variant",choices=("nano","turbo","v3"));
    for side in ("a","b"):
        p.add_argument(f"--{side}-t3",required=True,choices=WEIGHT_TYPES); p.add_argument(f"--{side}-s3",required=True,choices=WEIGHT_TYPES)
    p.add_argument("--reference",default=str(ROOT/"reference.wav")); p.add_argument("text"); p.add_argument("language",nargs="?"); a=p.parse_args(argv[1:])
    if a.variant=="v3" and not a.language: p.error("v3 requires a language")
    if a.variant!="v3" and a.language: p.error(f"{a.variant} does not accept a language")
    ref=Path(a.reference).resolve();
    if not ref.is_file(): raise SystemExit(f"reference WAV not found: {ref}")
    ra,ca=run_profile(a.variant,a.text,a.language,ref,a.a_t3,a.a_s3); rb,cb=run_profile(a.variant,a.text,a.language,ref,a.b_t3,a.b_s3)
    out=Path.cwd().resolve()/datetime.now().astimezone().strftime("precision-ab-%Y%m%d-%H%M%S"); out.mkdir()
    py=analysis_python(); proc=subprocess.run([str(py),str(ROOT/"tools/compare_runs.py"),str(ra),str(rb)],cwd=str(out),capture_output=True,text=True,encoding="utf-8")
    if proc.returncode not in (0,1): raise RuntimeError(proc.stderr.strip())
    result={"variant":a.variant,"language":a.language,"text":a.text,"reference":str(ref),"a":{"t3_weight_type":a.a_t3,"s3_weight_type":a.a_s3,"run_dir":str(ra),"command":ca},"b":{"t3_weight_type":a.b_t3,"s3_weight_type":a.b_s3,"run_dir":str(rb),"command":cb},"comparison":json.loads(proc.stdout)}
    dest=out/"precision_ab.json"; dest.write_text(json.dumps(result,indent=2,sort_keys=True,ensure_ascii=True,allow_nan=False)+"\n",encoding="utf-8"); print(dest); return 0
if __name__ == "__main__": raise SystemExit(main(sys.argv))
