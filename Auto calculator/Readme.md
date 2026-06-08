# Auto Calculator

A small collection of Python calculators for organic semiconductor laser (DFB/OSSL) research and general lab data processing. They cover waveguide thickness design, spin-coating speed estimation, ASE modelling, and basic statistics.

## Setup

Install the dependencies (Python 3.10+ recommended):

```bash
pip install -r requirements.txt
```

This installs `numpy`, `scipy`, `matplotlib`, and `customtkinter`.

## Programs

### 1. DFB OSSL thickness calculator.py

A graphical (GUI) tool for designing the organic film thickness of a DFB (Distributed Feedback) organic laser.

**What it does**
- Solves the slab-waveguide dispersion relation to get the effective index `n_eff` vs. film thickness.
- Calculates the cut-off thicknesses for the TE0, TE1, and TM0 modes.
- Given a target ASE wavelength, grating period, and stopband width, finds the film thickness that places lasing at the long- or short-wavelength edge of the stopband (stopband-corrected Bragg condition).
- Optionally converts the target thickness into a spin-coating speed (RPM) from a reference thickness/speed pair, using `RPM_new = RPM_ref * (t_ref / t_target)^2`.
- Plots the dispersion curve with the stopband and the matching thickness highlighted.
- Remembers your last inputs between sessions (saved to `dfb_OSSL_settings.json`).

**How to use**
```bash
python "DFB OSSL thickness calculator.py"
```
Enter the target wavelength, organic film index `n2`, and stopband width. Optionally enter a reference thickness and speed for the RPM suggestion, then click **CALCULATE**.

> Note: `n1` (glass, 1.52) is fixed; `n3` (air) and the grating period are editable. The settings file path is hard-coded to `C:\My files\Programs_codes` — adjust `CONFIG_FILE` in the script if you run it elsewhere.

### 2. DFB OSSL thickness calculator - mobile.py

A lightweight, command-line (CLI) version of the DFB thickness calculator above — no GUI, so it runs anywhere a terminal is available.

**What it does**
- Same core physics: finds the optimal film thickness for a target ASE wavelength given `n2`, grating period, and stopband.
- Reports the resulting lasing wavelength and the TE0/TE1 cut-off thicknesses.
- Optionally calculates the required spin-coating speed from a reference thickness/speed pair.

**How to use**
```bash
python "DFB OSSL thickness calculator - mobile.py"
```
Press **Enter** to accept the default shown in brackets, type a value to override, or type **q** to quit. After the thickness result, you can enter a reference thickness and speed for the RPM suggestion (or press Enter to skip).

### 3. Spin speed calculator.py

A CLI tool that fits spin-coating calibration data to a power law and predicts the speed needed for a target film thickness.

**What it does**
- Fits your `(speed, thickness)` data to the model `t = k * w^(-alpha)` and reports the volatility exponent `alpha` and the goodness of fit (R²).
- Inverts the model to find the spin speed for a target thickness.
- With a single data point it assumes `alpha = 0.5`; three or more points are needed for a real fit.
- Warns when the target speed falls outside your calibrated range or when the fit looks unphysical.

**How to use**
```bash
python "Spin speed calculator.py"
```
Enter pairs of `speed thickness` (any separators work, e.g. `1000 270; 1150,245 | 1500 180`), then enter the target thickness in nm. Type **q** to quit.

### 4. Ave_and_Std.py

A simple CLI statistics helper.

**What it does**
- Takes a list of numbers and prints the mean and sample standard deviation.
- Requires at least 3 numbers.
- Remembers the last set of numbers you entered (saved to `last_input.json`).

**How to use**
```bash
python Ave_and_Std.py
```
Enter at least 3 numbers separated by spaces or commas. Type **q** to quit.

### 5. ASE S shape caiculation.py

A batch (non-interactive) script that simulates the ASE (Amplified Spontaneous Emission) input/output S-curve using the Ganiel model.

**What it does**
- Solves a boundary-value problem for the forward/backward intensities in a gain medium across a range of pump rates.
- Produces the output intensity vs. pump rate ("S-curve") data.
- Saves the result as `ase_s_curve_data.csv` (pump rate vs. output intensity).

**How to use**
1. Edit the `params` dictionary at the top of the file to match your material (concentration, cross-sections, lifetime, etc.).
2. Run it:
```bash
python "ASE S shape caiculation.py"
```
The CSV is written to a hard-coded path (`C:\My files\Google drive sync\St Andrews\Data`); change `save_dir` in the script if needed. If that folder is unavailable it falls back to the current directory.

## Supporting files

- `requirements.txt` — Python package dependencies.
- `dfb_laser_settings.json` — example/saved settings for the DFB GUI calculator.
