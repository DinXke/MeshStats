# PlatformIO pre-script voor de MeshTrack-overlay (0.9.7).
#
# De overlay wordt gebouwd via build_src_filter "+<../../meshtrack/firmware/meshtrack/*.cpp>". Die
# objecten komen daardoor in <build_dir>/meshtrack/... terecht, BUITEN de map van de env, en worden
# gedeeld door alle envs (t1000e_meshtrack, rak_wismesh_tag_meshtrack). Een build van het ene bord
# zou zo objecten van het andere bord kunnen linken. Dit script wist die gedeelde map als de vorige
# build voor een andere env was (stempel <build_dir>/meshtrack/.pioenv).
import os
import shutil

Import("env")  # noqa: F821  (SCons)

build_root = env.subst("$PROJECT_BUILD_DIR")  # noqa: F821
shared = os.path.join(build_root, "meshtrack")
stamp = os.path.join(shared, ".pioenv")
cur = env["PIOENV"]  # noqa: F821

prev = None
try:
    with open(stamp, encoding="utf-8") as f:
        prev = f.read().strip()
except OSError:
    pass

if prev != cur:
    if os.path.isdir(shared):
        print(f"MeshTrack: gedeelde overlay-objecten van '{prev}' gewist (nu '{cur}')")
        shutil.rmtree(shared, ignore_errors=True)
    os.makedirs(shared, exist_ok=True)
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(cur)
