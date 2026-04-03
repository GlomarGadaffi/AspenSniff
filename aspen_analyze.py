#!/usr/bin/env python3
"""
aspen_analyze.py
Host-side analysis of aspen_sweep.ino output.

Usage:
  # Live capture:
  python3 -m serial.tools.miniterm /dev/ttyUSB0 115200 | tee sweep.jsonl

  # Analyze accumulated data:
  python3 aspen_analyze.py sweep.jsonl

  # Live analysis (tail -f equivalent):
  python3 aspen_analyze.py --follow sweep.jsonl
"""

import json
import sys
import time
import argparse
import collections
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser(description="Analyze aspen_sweep JSONL output")
    p.add_argument("file", help="JSONL log file from sweep")
    p.add_argument("--follow", action="store_true",
                   help="Tail file and re-print summary every N seconds")
    p.add_argument("--interval", type=float, default=30.0,
                   help="Summary refresh interval in seconds when --follow (default 30)")
    p.add_argument("--min-hits", type=int, default=2,
                   help="Minimum CAD hits to include freq in output (default 2)")
    return p.parse_args()


def load_events(path: Path) -> list[dict]:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith('{'):
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def summarize(events: list[dict], min_hits: int) -> None:
    cad_hits    = collections.Counter()   # freq → count
    rx_ok       = collections.defaultdict(list)  # (freq, sf, bw, sync) → [rssi]
    rx_fail     = collections.defaultdict(list)  # (freq, sf, bw, sync) → [rssi]
    rssi_by_freq = collections.defaultdict(list) # freq → [rssi from cad_hit]
    sweep_count = 0
    decoded_payloads = []

    for e in events:
        ev = e.get("e")
        if ev == "cad_hit":
            freq = round(e["freq"], 3)
            cad_hits[freq] += 1
            if "rssi" in e:
                rssi_by_freq[freq].append(e["rssi"])
        elif ev == "rx_ok":
            key = (round(e["freq"], 3), e.get("sf"), e.get("bw"), e.get("sync"))
            rx_ok[key].append(e.get("rssi", 0))
            if e.get("hex"):
                decoded_payloads.append(e)
        elif ev == "rx_fail":
            key = (round(e["freq"], 3), e.get("sf"), e.get("bw"), e.get("sync"))
            rx_fail[key].append(e.get("rssi", 0))
        elif ev == "sweep_done":
            sweep_count = max(sweep_count, e.get("sw", 0))

    total_sweeps = sweep_count + 1

    # ── Frequency Heatmap ──────────────────────────────────────────────────
    print(f"\n{'═'*64}")
    print(f"  ASPEN GROVE SWEEP ANALYSIS  |  {total_sweeps} sweeps  |  "
          f"{len(events)} events")
    print(f"{'═'*64}")

    active_freqs = [(f, c) for f, c in cad_hits.items() if c >= min_hits]
    active_freqs.sort(key=lambda x: -x[1])

    if not active_freqs:
        print(f"\n  No frequencies with ≥{min_hits} CAD hits. Still collecting.\n")
    else:
        print(f"\n  CAD HITS BY FREQUENCY  (≥{min_hits} hits, sorted by count)\n")
        max_count = active_freqs[0][1] if active_freqs else 1
        bar_max = 40
        for freq, count in active_freqs[:30]:
            rssis = rssi_by_freq.get(freq, [])
            avg_rssi = int(sum(rssis) / len(rssis)) if rssis else 0
            bar = "█" * int(count / max_count * bar_max)
            print(f"  {freq:7.3f} MHz  {bar:<{bar_max}}  {count:4d}  {avg_rssi:4d} dBm")

    # ── SF/BW Decode Matrix ────────────────────────────────────────────────
    if rx_ok or rx_fail:
        print(f"\n  SF/BW PROBE RESULTS\n")
        print(f"  {'Freq':>9}  {'SF':>3}  {'BW':>6}  {'Sync':>6}  "
              f"{'OK':>4}  {'Fail':>4}  {'AvgRSSI':>8}")
        print(f"  {'-'*60}")

        all_keys = set(rx_ok) | set(rx_fail)
        for key in sorted(all_keys, key=lambda k: (-len(rx_ok.get(k, [])), k)):
            freq, sf, bw, sync = key
            oks   = rx_ok.get(key, [])
            fails = rx_fail.get(key, [])
            all_rssi = oks + fails
            avg = int(sum(all_rssi) / len(all_rssi)) if all_rssi else 0
            flag = " ◄ DECODED" if oks else ""
            print(f"  {freq:9.3f}  {sf:3}  {bw:6.1f}  {sync:>6}  "
                  f"{len(oks):4}  {len(fails):4}  {avg:8} dBm{flag}")

    # ── Decoded Payloads ───────────────────────────────────────────────────
    if decoded_payloads:
        print(f"\n  DECODED PAYLOADS  ({len(decoded_payloads)} clean CRC)\n")
        for d in decoded_payloads[:20]:
            print(f"  t={d.get('t')}ms  {d.get('freq'):.3f}MHz  "
                  f"SF{d.get('sf')}/BW{d.get('bw')}  "
                  f"rssi={d.get('rssi')}  snr={d.get('snr'):.1f}")
            print(f"    {d.get('hex', '')}")
            if len(decoded_payloads) > 20:
                print(f"  … {len(decoded_payloads)-20} more")

    # ── Recommended Next Step ──────────────────────────────────────────────
    print(f"\n  RECOMMENDATION\n")
    if decoded_payloads:
        d = decoded_payloads[0]
        print(f"  Clean decode found. Lock in parameters:")
        print(f"    freq={d.get('freq'):.3f} MHz  SF={d.get('sf')}  "
              f"BW={d.get('bw')} kHz  sync={d.get('sync')}")
        print(f"  Next: targeted listener + payload structure analysis.")
    elif active_freqs:
        top5 = [f for f, _ in active_freqs[:5]]
        print(f"  CAD activity found but no clean decode yet.")
        print(f"  Hot channels: {', '.join(f'{f:.3f}' for f in top5)} MHz")
        print(f"  Consider: extend RX_DWELL_MS, try CR 4/6 or 4/7,")
        print(f"  or attempt preamble sniff with SDR on these exact freqs.")
    else:
        print(f"  No significant activity yet. Let it run longer.")
        print(f"  goTenna devices sleep aggressively between messages.")
    print()


def main():
    args = parse_args()
    path = Path(args.file)

    if args.follow:
        print(f"Following {path} — summary every {args.interval}s. Ctrl-C to stop.")
        try:
            while True:
                if path.exists():
                    events = load_events(path)
                    summarize(events, args.min_hits)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            sys.exit(1)
        events = load_events(path)
        summarize(events, args.min_hits)


if __name__ == "__main__":
    main()
