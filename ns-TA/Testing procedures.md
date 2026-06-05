# Testing procedure of ns-TA and software design

**Sample film should be there during test**
## Step
- 1. No lamp, no laser, one scan of spectra - I_bkg
Ocsilliscope: auto trigger

- 2. No laser, lamp on, one scan, - I_1
Ocsilliscope: auto trigger

- 3. No lamp, Laser on, one scan, - I_2 = I_PL
Ocsilliscope: trigger with laser sync signal

- 4. Laser and lamp both on, one scan - I_3
**Ocsilliscope**: trigger via delay generator
Delay gen: trigger with laser sync signal, rising edge
**Delay time**: set on the delay gen, can set multiple delay time

## Data received
Data received are voltages from osci, a calibration is required to convert it into optical intensity.

**Calculation**: 4 sets of spectrum data collected, $\Delta OD = -log_{10}((I_3-I_2)/(I_1-I_{bkg}))$