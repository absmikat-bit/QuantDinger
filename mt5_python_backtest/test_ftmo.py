from datetime import datetime

FILE = "FTMO XAUUSD_M1_202208290105_202609302349.csv"

count = 0
first = None
last = None

with open(FILE, "r", encoding="utf-8-sig") as f:

    for line in f:

        line = line.strip()

        if not line or line.startswith("<DATE>"):
            continue

        parts = line.split()

        if len(parts) < 9:
            continue

        try:
            dt = datetime.strptime(
                parts[0] + " " + parts[1],
                "%Y.%m.%d %H:%M:%S"
            )

            count += 1

            if first is None:
                first = dt

            last = dt

        except:
            pass

print()
print("====================================")
print("FTMO DATA TEST")
print("====================================")
print(f"Bars loaded : {count:,}")
print(f"First bar   : {first}")
print(f"Last bar    : {last}")
print("====================================")

