#!/usr/bin/env python3
"""
aspen_tripwire.py
Squelch-triggered I/Q capture for transient Aspen Grove mesh bursts.

Dependencies: 
pip install pyrtlsdr numpy
"""

import numpy as np
from rtlsdr import RtlSdr
import time
from pathlib import Path
from datetime import datetime

# --- Configuration ---
CENTER_FREQ = 915e6      # Staring at the middle of the ISM band to start
SAMPLE_RATE = 2.4e6      # Max stable rate for standard RTL-SDR
GAIN = 'auto'            # Or set to a specific dB value (e.g., 35)
READ_SIZE = 1024 * 256   # ~100ms chunks at 2.4Msps
SQUELCH_DB_ABOVE_NOISE = 12.0 # Trigger threshold
OUTPUT_DIR = Path("aspen_captures")

def get_block_power(samples):
    """Calculate relative power of the I/Q block in dB."""
    # Magnitude squared
    mag_sq = np.real(samples)**2 + np.imag(samples)**2
    mean_power = np.mean(mag_sq)
    if mean_power == 0:
        return -100.0
    return 10 * np.log10(mean_power)

def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    sdr = RtlSdr()
    sdr.sample_rate = SAMPLE_RATE
    sdr.center_freq = CENTER_FREQ
    sdr.gain = GAIN

    print(f"[*] Arming tripwire at {CENTER_FREQ/1e6:.2f} MHz")
    print("[*] Calibrating noise floor... keep the frequency clear.")
    
    # Let the AGC settle and calculate the baseline noise floor
    sdr.read_samples(READ_SIZE) 
    baseline_samples = []
    for _ in range(5):
        baseline_samples.extend(sdr.read_samples(READ_SIZE))
    
    noise_floor = get_block_power(np.array(baseline_samples))
    trigger_level = noise_floor + SQUELCH_DB_ABOVE_NOISE
    
    print(f"[*] Noise floor estimated at: {noise_floor:.2f} dB")
    print(f"[*] Squelch trigger set to:   {trigger_level:.2f} dB")
    print("[*] Listening for bursts...\n")

    try:
        while True:
            # Read a chunk of I/Q data
            samples = sdr.read_samples(READ_SIZE)
            power = get_block_power(samples)

            if power >= trigger_level:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
                filename = OUTPUT_DIR / f"burst_{timestamp}_{CENTER_FREQ/1e6:.1f}MHz.iq"
                
                print(f"[!] TRIGGER: Power {power:.2f} dB. Capturing burst!")
                
                # We tripped. Grab the next few chunks immediately to ensure 
                # we capture the whole 300ms+ transmission tail.
                extended_samples = list(samples)
                for _ in range(3):
                    extended_samples.extend(sdr.read_samples(READ_SIZE))
                
                # Convert to complex64 for Universal Radio Hacker / Inspectrum
                out_data = np.array(extended_samples).astype(np.complex64)
                out_data.tofile(filename)
                
                print(f"[*] Saved to {filename}")
                
                # Brief pause so we don't trigger 50 times on the same long packet
                time.sleep(0.5) 
                
    except KeyboardInterrupt:
        print("\n[*] Disarming tripwire.")
    finally:
        sdr.close()

if __name__ == "__main__":
    main()
