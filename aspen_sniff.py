#!/usr/bin/env python3
"""
aspen_autopsy.py
Automated extraction of Bandwidth and Spreading Factor from LoRa/Aspen Grove I/Q bursts.

Dependencies:
pip install numpy scipy
"""

import sys
import numpy as np
from scipy import signal
import argparse

def analyze_burst(iq_file, sample_rate):
    # 1. Load the raw complex64 I/Q data
    print(f"[*] Loading {iq_file}...")
    try:
        data = np.fromfile(iq_file, dtype=np.complex64)
    except Exception as e:
        print(f"[!] Failed to load file: {e}")
        return

    if len(data) == 0:
        print("[!] File is empty.")
        return

    # 2. Compute the Spectrogram (STFT)
    # nperseg defines our frequency/time resolution trade-off. 
    # 1024 is usually a good sweet spot for 2.4Msps.
    nperseg = 1024
    f, t, Zxx = signal.stft(data, fs=sample_rate, nperseg=nperseg, return_onesided=False)
    
    # Shift zero-frequency to the center
    f = np.fft.fftshift(f)
    Zxx = np.fft.fftshift(Zxx, axes=0)
    
    # Convert power to dB
    power = np.abs(Zxx)**2
    power_db = 10 * np.log10(power + 1e-12) # Add tiny offset to avoid log(0)

    # 3. Estimate Bandwidth (BW)
    # Find the noise floor and look for the frequency bins that stay consistently above it
    noise_floor = np.median(power_db)
    active_bins = np.mean(power_db, axis=1) > (noise_floor + 10) # 10dB threshold
    
    if not np.any(active_bins):
        print("[!] No distinct signal found above noise floor.")
        return

    # Get the min and max frequencies of the active bins
    active_freqs = f[active_bins]
    bw_hz = np.max(active_freqs) - np.min(active_freqs)
    
    # Snap to the nearest standard LoRa bandwidth (125kHz, 250kHz, 500kHz)
    standard_bws =
    estimated_bw = min(standard_bws, key=lambda x: abs(x - bw_hz))
    
    print(f"[+] Raw BW calculation: {bw_hz / 1000:.2f} kHz")
    print(f"[+] Snapped LoRa BW:    {estimated_bw / 1000:.0f} kHz")

    # 4. Estimate Spreading Factor (SF)
    # We need to find the chirp rate. A LoRa preamble consists of multiple unmodulated up-chirps.
    # We look for the strongest frequency peak over time and measure how long it takes to sweep across the BW.
    
    # Find the peak frequency index for each time slice
    peak_freq_indices = np.argmax(power_db, axis=0)
    peak_freqs = f[peak_freq_indices]

    # Calculate the derivative of the frequency over time (the slope of the chirp)
    dt = t[1] - t
    df_dt = np.gradient(peak_freqs, dt)
    
    # Isolate the constant positive slopes (the up-chirps in the preamble)
    # A valid up-chirp sweeps exactly BW Hz per Symbol Time (Ts)
    positive_slopes = df_dt[df_dt > 0]
    
    if len(positive_slopes) == 0:
        print("[!] Could not detect distinct up-chirps.")
        return
        
    median_slope = np.median(positive_slopes)
    
    # If slope = BW / Ts, then Ts = BW / slope
    # We use the snapped BW for cleaner math
    t_s = estimated_bw / median_slope 
    
    # Calculate SF using: SF = log2(Ts * BW)
    sf_raw = np.log2(t_s * estimated_bw)
    estimated_sf = round(sf_raw)

    print(f"[+] Symbol Time (Ts):   {t_s * 1000:.2f} ms")
    print(f"[+] Raw SF calculation: {sf_raw:.2f}")
    print(f"[+] Snapped LoRa SF:    {estimated_sf}")
    
    print(f"\n[>>>] TARGET ACQUIRED: SF{estimated_sf} / BW{estimated_bw/1000:.0f}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract SF and BW from LoRa I/Q burst.")
    parser.add_argument("file", help="Path to the complex64 .iq file")
    parser.add_argument("--rate", type=float, default=2.4e6, help="Sample rate in Hz (default: 2.4e6)")
    args = parser.parse_args()
    
    analyze_burst(args.file, args.rate)
