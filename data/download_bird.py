"""Download the BIRD dev set (dev_20240627) into data/bird/.

Result:  data/bird/dev.json                      1534 questions + gold SQL + evidence
         data/bird/dev_databases/<db>/<db>.sqlite  11 databases (~1.5 GB)
The download is 346 MB; ~2.2 GB of disk is needed while unpacking, then the zips are deleted.
Usage: python data/download_bird.py
"""
import shutil
import urllib.request
import zipfile
from pathlib import Path

URL = "https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip"
DEST = Path(__file__).parent / "bird"


def main() -> None:
    if (DEST / "dev.json").exists() and (DEST / "dev_databases").exists():
        print(f"Already present: {DEST}")
        return
    DEST.mkdir(exist_ok=True)
    outer = DEST / "dev.zip.part"
    print(f"Downloading {URL} (346 MB) ...")
    urllib.request.urlretrieve(URL, outer)

    with zipfile.ZipFile(outer) as z:  # contains dev_20240627/{dev.json, dev_databases.zip, ...}
        for info in z.infolist():
            name = Path(info.filename).name
            if name in ("dev.json", "dev_tables.json", "dev_databases.zip"):
                with z.open(info) as src, open(DEST / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    outer.unlink()

    inner = DEST / "dev_databases.zip"
    print("Unpacking databases (~1.5 GB) ...")
    with zipfile.ZipFile(inner) as z:
        # extractall drops absolute paths and '..' parts, so entries can't escape DEST.
        # Skip macOS resource-fork junk that some BIRD zips carry.
        z.extractall(DEST, members=[m for m in z.namelist() if "__MACOSX" not in m])
    inner.unlink()

    dbs = sorted(p.parent.name for p in (DEST / "dev_databases").glob("*/*.sqlite"))
    print(f"Saved {len(dbs)} databases to {DEST / 'dev_databases'}: {', '.join(dbs)}")


if __name__ == "__main__":
    main()
