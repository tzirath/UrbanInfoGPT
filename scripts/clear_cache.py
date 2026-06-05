import shutil
import os

cache_dir = "data/cache/queries"
if os.path.exists(cache_dir):
    shutil.rmtree(cache_dir)
    os.makedirs(cache_dir)
    print("Cache cleared.")
else:
    print("No cache to clear.")
