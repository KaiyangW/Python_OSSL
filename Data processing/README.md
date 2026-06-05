# Data Processing处理脚本集

> 最后更新：2026-05-26

## 📑 目录

- [ASE 文件夹](#ase-文件夹)
- [DFB devices文件夹](#dfb-devices-文件夹)
- [TRPL 文件夹](#TRPL-文件夹)
- [Python plot lib folder](#python-plot-lib-文件夹)

------------------------------------------------------------------------

## ASE 文件夹 {#ase-文件夹}

### 1. `ASE Threshold data processing ultra.py`

**用途**：处理 ASE 阈值数据的主脚本。 **使用方法**： 在图形界面中输入： - 定标（calibration）方程 - 光束尺寸（beam size） - 测量时 PD 上使用的 ND filter 数值

**关键参数**： - `Collapse factor`：控制 Savitzky‑Golay 平滑窗口的切换逻辑。当当前 FWHM 降至历史最大 FWHM 的 **60%** 以下时，自动切换平滑窗口。 - `平滑窗口范围`：可在界面中设置。 - `Auto_peak_window`：自动寻找峰值后的积分波长范围。下面两个数字仅为默认值，程序每次运行时会自动调整，**不建议手动修改**。

**特色算法**：包含用于抹除 PL 背景的 **wing match** 算法。 **输出**：处理后的数据文件（含 `auto fit` 字段，由 `auto_threshold_module` 生成）。

------------------------------------------------------------------------

### 2. `auto_threshold_module.py`

**用途**：负责自动拟合并给出 ASE 阈值的**子模块**。 **说明**：**无法独立运行**，由 `ASE Threshold data processing ultra.py` 内部调用。 **注意**: 必须和 `ASE Threshold data processing ultra.py`位于同一个文件夹中 **输出**：在最终保存的数据文件中，`auto fit` 列即由此模块生成。

------------------------------------------------------------------------

### 3. `Edge loss data process.py`

**用途**：处理 edge loss 数据。 **输出**：生成的最终文件可直接用于绘图并计算 loss 值。

------------------------------------------------------------------------

### 4. `Threshold data processing steps.py`

**用途**：**阶段展示版本**，用于演示 wing match 算法抹除 PL 背景的具体过程。 **使用方法**：选中任意文件夹后运行，脚本会生成四步中间图像，展示抹除过程中光谱的逐阶段变化。

------------------------------------------------------------------------

## DFB devices 文件夹 {#dfb-devices-文件夹}

### 1. `auto_threshold_module_laser.py`

**用途**：针对 DFB 激光器数据的自动阈值拟合模块（`auto_threshold_module` 的变体）。 **背景**：因 SA 测试用的激光光源极不稳定，原始数据含大量异常点。 **特性**：内置自动剔除不合理点的算法（虽非总是有效）。 **说明**：**无法独立运行**，需由主脚本调用。

------------------------------------------------------------------------

### 2. `Bilinear_fit.py`

**用途**：手动拟合脚本。 **使用方法**： 1. 运行后选择由 `ASE Threshold data processing ultra` 生成的ultra数据文件。 2. 在弹出的 UI 图中，用鼠标点击数据点，控制该点是否参与拟合。 3. 实时观察阈值及其他参数的变化。 4. 点击“保存”后，输出： - 一张拟合预览图片（PNG） - 一份可用于绘图的数据文件（xlsx）

------------------------------------------------------------------------

### 3. `Laser Threshold data processing.py`

**用途**：针对 DFB 数据的 ASE 主脚本变体。 **与 ASE 版本的区别**： - **无** PL 背景抹除算法。 - 保存的数据文件仅包含：**积分光强** + **经峰值优化的 FWHM 结果**。

------------------------------------------------------------------------

### 4. `Multi_linear_fit.py`

**用途**：手动拟合脚本。 **使用方法**： 和bilinear_fit.py类似，但是可以控制拟合直线有几段。

## TRPL 文件夹 {#trpl-文件夹}

### 1. `Auto_align_decay_data.py`

**用途**：处理时间分辨光致发光（TRPL）衰减曲线的对齐与归一化。

**主要功能**： - **自动峰值对齐**：识别两个文件的峰值位置并平移时间轴，确保动力学起始点完全重合。 - **强度与基线归一化**：缩放目标曲线强度并调整基线，使其峰值高度和背景噪声与基准文件一致。 - **负时间保护**：自动判断平移方向，避免时间轴出现负值，并给出最合理的对齐基准。 - **Origin 适配输出**：生成对比预览图 + 合并后的 CSV 文件，可直接拖入 Origin 进行拟合或绘图。

------------------------------------------------------------------------

### 2. `Draw multiple graphs.py`

**用途**：批量处理大量 TRPL 数据的进阶脚本。

**核心能力**： - **自动化批量对齐**：自动识别文件夹内所有 TRPL 衰减曲线，以**最晚的峰值时间**为基准，将所有曲线对齐并抹平基线。支持多进程并行处理。 - **智能 IRF 适配**：自动识别仪器响应函数（IRF）文件，并为每条 TRPL 曲线生成一份在**时间、基线、峰值**上完全匹配的定制化 IRF，极大方便后续卷积拟合。 - **光谱自动分析**：针对发射光谱（Emission Scan）数据，自动平滑、寻峰、计算半高宽（FWHM），并在预览图中直接标注。 - **一键整理导出**：为每个原始数据生成 PNG 预览图，并将该文件夹下所有处理好的数据（TRPL、适配后的 IRF、光谱）合并导出为一个 `combined_data.csv` 文件。

------------------------------------------------------------------------

### 3. `Recon_fit_process.py`

**用途**: `Recon_fit_stretched.py` 和 `lifetime_fit.py` 的核心数学引擎，专门负责底层的物理计算。 **核心能力**： - **反卷积算法**：利用 FFT 卷积将仪器响应函数（IRF）与衰减模型结合，通过最小二乘法（Least Squares）实现对 TCSPC 数据的精确拟合。 - **伸缩指数物理模型**：支持多组分伸缩指数（Stretched Exponential）拟合，自动计算平均寿命 ($\langle \tau \rangle$)。 - **加权平均寿命分析**：支持计算**光强加权平均寿命** (Intensity-averaged lifetime, $\tau_{eff}$)，用于界面显示和直观对比。 - **动力学速率计算逻辑**：在后台自动计算**数量加权平均寿命** (Number-averaged lifetime, $\tau_{mean}$)，并将其作为输入传递给速率计算模块，以确保 $k_{RISC}$ 等常数的物理准确性。相关参数以 `Hidden_Number_Mean_Avg_Tau` 为名导出。 - **PF/DF 定量分析**：内置“面积差值法”，能自动分离并计算瞬时荧光（PF）、延迟荧光（DF）及磷光的相对比例和贡献。

------------------------------------------------------------------------

### 4. `lifetime_fit.py`

**用途**: **统一的 TRPL 拟合交互式主程序 (GUI)**，整合了 Reconvolution Fit 和 Tail Fit。

**核心能力**： - **双模式切换**：启动时可自由选择使用反卷积拟合（Reconvolution）或传统的尾部拟合（Tail Fit）。 - **智能 IRF 管理**：在反卷积模式下，支持自动搜索并匹配 IRF 文件（以 `IRF` 命名的 CSV），大幅提升效率。 - **功能全集成**：整合了参数配置、区间调整、加权平均寿命计算以及**动力学速率分析 (Kinetic Analysis)** 模式，可一键导出完整拟合报告。 - **参数导出一致性**：自动将 UI 显示的 $\tau_{eff}$ 和动力学专用的 $\tau_{mean}$ 同时存入 Excel 结果文件，方便后续复核。

------------------------------------------------------------------------

### 5. `RISC_exact_solution_coremath.py` & `RISC_exact_solution.py`

**用途**：基于物理模型精确解析解（Exact Solution）的 TADF 动力学速率计算核心引擎与工具。

**核心功能**：

- **核心数学引擎 (`coremath.py`)**：实现了基于 Cardano 公式的三次方程精确求解逻辑。**注意**：该引擎现在严格要求输入**数量加权寿命 (**$\tau_{mean}$)，而非光强加权寿命，以符合速率常数的物理定义。
- **精确动力学求解**：能从实验数据中提取 $k_{RISC}$、$k_{ISC}$、$k_r$ 和 $k_{nr}$ 等关键速率常数。相比传统近似公式，在延迟分量比例较高时更精确。
- **自动分量校正**：内置针对延迟荧光（DF）分量的校正逻辑，自动计算 $\Phi_{PF}$、$\Phi_{DE}$、$\Phi_{DF}$ 等物理指标。
- **数据持久化**：支持将输入参数和结果保存至 JSON，方便重复调用。

**使用方法**：

1.  **自动模式**：通过 `lifetime_fit.py` 运行拟合后，系统会自动调用 coremath 引擎。
2.  **手动模式 (`RISC_exact_solution.py`)**：启动图形化交互界面，手动输入 Total PLQY、PF/DF 比例以及相应的 $\tau_p / \tau_d$（建议使用拟合得到的 Mean Tau）。
3.  **查看结果**：实时显示校正后的速率常数（提供 $s^{-1}$ 速率和对应的寿命时间单位）。

------------------------------------------------------------------------

### 6. `Tail_fit_process.py`

**用途**: `lifetime_fit.py` 的核心数学引擎，负责尾部拟合的物理计算。 **核心能力**： - **纯指数/伸缩指数拟合**：支持对衰减曲线尾部进行多组分拟合，无需 IRF 卷积。 - **逻辑对齐**：具备与 Recon 版本完全一致的平均寿命分析逻辑（$\tau_{eff}$ 用于 UI，$\tau_{mean}$ 用于动力学）与 PF/DF 比例计算功能。

------------------------------------------------------------------------

### 7. `trpl_processor.py`

**用途**：TRPL 数据的批量预处理与可视化入口脚本，整合对齐、归一化及绘图功能。 **核心功能**：一键读取文件夹内所有 CSV，自动对齐峰值并生成合并数据文件。 **注意**：无法独自运行，这是 `Draw multiple graphs.py` 的核心数学引擎。

### 8. `Voigt_PL.py`

**用途**：对稳态 PL 发射光谱做 **Voigt 轮廓拟合**，支持三种分析模式（启动时手动选择，或通过 `--mode` 指定）：

| 模式 | `--mode` | 说明 |
|--------------------|--------------------------------|--------------------|
| Gamma 敏感性扫描 | `sensitivity` | 全文件夹单峰 Voigt，扫描固定 $\gamma$ 下的 $\sigma$ |
| 77K 双振动峰拟合 | `77k-dual` | 单条 77K 光谱，0-0 + 0-1 双 Voigt 叠加 |
| RT + 77K 全局联立 | `global` | 手动各选 RT、77K 光谱，共享 $\sigma$，独立 $\gamma$ |

**数据预处理**（三种模式共用）：读取 `.csv` 光谱（第一列波长 nm，第二列强度），转换为能量坐标 $E = 1239.8 / \lambda$；强度按雅可比修正 $I_E = I_\lambda \cdot \lambda^2$。自动估计峰位与 FWHM，在 $[E_{peak} - 0.7 \times \mathrm{FWHM},\; E_{peak} + 1.2 \times \mathrm{FWHM}]$ 局部窗口内拟合。文件名含 `Gated` 的 CSV 自动跳过。基线固定为 0。

**使用方法**：

1.  运行 `Voigt_PL.py`，依次选择数据文件夹与分析模式；或在命令行指定目录与模式。
2.  模式 2、3 会弹出列表框，**手动选择**要拟合的 CSV（不再根据文件名自动识别 RT/77K）。
3.  命令行示例：

``` bash
# 弹窗选文件夹 + 选模式
python Voigt_PL.py "数据文件夹"

# 模式 1：gamma 敏感性扫描（全文件夹，单峰）
python Voigt_PL.py "数据文件夹" --mode sensitivity
python Voigt_PL.py "数据文件夹" --mode sensitivity --workers 4

# 模式 2：77K 双 Voigt（手动或指定 CSV）
python Voigt_PL.py "数据文件夹" --mode 77k-dual
python Voigt_PL.py "数据文件夹" --mode 77k-dual --csv "PL_330ex_77K_vac.csv"

# 模式 3：RT + 77K 全局联立
python Voigt_PL.py "数据文件夹" --mode global
python Voigt_PL.py "数据文件夹" --mode global --rt-csv "xxx_RT.csv" --77k-csv "xxx_77K.csv"
```

`--global-fit` 已弃用，等同 `--mode global`。

------------------------------------------------------------------------

#### 模式 1：`sensitivity`（Gamma 敏感性扫描）

**拟合逻辑**：单峰 `VoigtModel`；对每个光谱在 $\gamma = 10\text{--}30\ \mathrm{meV}$（步长 2 meV）下固定 $\gamma$ 拟合，记录 $\sigma$、中心、$R^2$ 等。`ProcessPoolExecutor` 并行，`tqdm` 进度条。

**输出**（`Voigt Output/`）：

- `voigt_gamma_sensitivity_summary.csv`：每个 `(文件名, gamma)` 一行（**扫描表格式**）。
- `{样本名}_gamma20meV_fit.png`：$\gamma = 20\ \mathrm{meV}$ 单峰拟合图（Voigt + 高斯/洛伦兹分解）。
- `sigma_vs_gamma_sensitivity.png` / `.svg`：$\sigma$–$\gamma$ 敏感性折线图。

------------------------------------------------------------------------

#### 模式 2：`77k-dual`（77K 双振动峰）

**适用场景**：低温 PL 出现明显 0-0 / 0-1 振动旁带，单峰 $R^2$ 偏低。

**模型**：$I = \mathrm{Voigt}_{00} + \mathrm{Voigt}_{01}$

- 两峰共享同一 $\sigma$、同一 $\gamma$（固定为 20 meV，与扫描参考值一致）。
- `center_01 = center_00 - E_\mathrm{vib}`，$E_\mathrm{vib}$ 约束在 **0.15–0.18 eV**（典型 C–C 骨架伸缩）。

**输出**（`Voigt Output/`）：

- `{样本名}_77k_dual_voigt_summary.csv`：**专用参数表**（`parameter / value / stderr / note`，**不是**扫描表）。
- `{样本名}_77k_dual_voigt_fit.png`：蓝虚线 0-0、橙虚线 0-1、红实线总拟合（含 $R^2$）。

**注意**：请勿用模式 1 的 `voigt_gamma_sensitivity_summary.csv` 解读 77K 双峰结果；77K 振动分析应使用本模式。

------------------------------------------------------------------------

#### 模式 3：`global`（RT + 77K 全局联立）

**模型**：

- **RT**：单 Voigt。
- **77K**：双 Voigt（0-0 + 0-1），约束同模式 2。
- **共享** `sigma_global`（RT 与 77K 两峰共用）；`gamma_RT`、`gamma_77K` 独立，且 `gamma_77K < gamma_RT`。

**输出**（`Voigt Output/`）：

- `global_voigt_fit_summary.csv`：全局参数汇总。
- `global_RT_77K_voigt_fit.png`：上下两 panel（RT 单峰；77K 双峰 + 分量虚线）。

------------------------------------------------------------------------

#### 出图样式

- 图幅 20 cm × 15 cm，16 pt 字体；仅左、下轴刻度（上、右无刻度）。
- 敏感性折线图例置于绘图区右侧外部，`savefig(..., bbox_inches='tight')` 防止裁切。

**依赖**：`numpy`、`pandas`、`matplotlib`、`lmfit`、`scipy`、`tqdm`；图形界面需 `tkinter`。

**相关脚本**：文件名含 `gated` 的稳态 PL **散射背景减除**见 `Away_gated_diffraction.py`（与 Voigt 分析独立）。

------------------------------------------------------------------------

### 9. `Away_gated_diffraction.py`

**用途**：对**文件名含 `gated`** 的稳态 PL CSV 做参考谱减除，用于削弱衍射/散射等宽带背景，便于对比不同样品。光谱读取逻辑与 `Voigt_PL.py` 中的 `load_pl_csv` 一致（Jasco / 通用两列格式）。

**参考谱**：固定读取与脚本同目录下的 `Gated PL 330ex 2ms-100ms RT air.csv`（该文件不会作为待处理样品重复扫描）。

**对齐与减除**：默认在波长窗口 **400–420 nm** 内，用 `np.mean()` 分别对样品与参考求平均强度 $\bar{I}_\mathrm{sample}$、$\bar{I}_\mathrm{ref}$，定义缩放因子 $S = \bar{I}_\mathrm{sample} / \bar{I}_\mathrm{ref}$；将参考谱插值到样品波长网格后，按 $Y_\mathrm{corrected} = Y_\mathrm{sample} - S \times Y_\mathrm{ref}$ 得到修正谱。

**使用方法**：

1.  将待处理的 gated 样品 CSV 放在同一数据文件夹中（与参考谱格式一致即可）。
2.  在 `TRPL/PL` 目录运行 `Away_gated_diffraction.py`，在对话框中选择该数据文件夹；或命令行传入数据目录路径。
3.  可选参数：`--align-min`、`--align-max` 覆盖默认对齐窗口（nm）。

**输出**：在数据目录下新建 **`Away Gated Output`** 文件夹，包含：

- 每个样品一份 `*_corrected.csv`：波长、原始样品强度、缩放后的参考、修正后强度；**文件名与图标题中均含** $S$，便于核对散射差异。
- 每个样品一份预览 PNG：样品、缩放参考、修正谱及对齐窗口高亮。
- `gated_background_subtraction_summary.csv`：汇总各文件的 $S$、`mean_sample`、`mean_ref` 及对齐区间。

**依赖与路径**：脚本通过 `from Voigt_PL import load_pl_csv` 复用读取逻辑，请在 **`TRPL/PL` 目录下**运行本脚本（或将工作目录设为含 `Voigt_PL.py` 的文件夹），否则可能无法导入模块。

------------------------------------------------------------------------

### 10. `PL_onset_runner.py`（PL 起峰 / onset 检测）

**路径**：`TRPL/PL/`（同目录含 `onset_calculator.py`、`plotter.py`、`filters.py`）。

**用途**：从稳态 PL 发射光谱（Jasco 等导出的 CSV/TXT）自动检测 **起峰波长 / 能量**，并生成带 baseline、切线与 onset 标记的验证图。支持单谱分析与荧光 / 磷光双谱对比。

**算法（Tangent–Baseline Intersection，能量域计算）**：

1.  读取 CSV 中前两列数值行（默认：波长 nm + 强度 counts），忽略文件头元数据。
2.  将 $I(\lambda)$ 转换为光子能量坐标，并做雅可比修正：
    $$I(E) = I(\lambda)\,\frac{\lambda^2}{hc},\qquad hc = 1240\ \mathrm{eV{\cdot}nm}$$
3.  在 **能量域** 对 $I(E)$ 做 Savitzky–Golay 平滑，取导数最大处作切线，与 baseline 求交得到 onset。
4.  **Baseline**：排序后高能端前 **10%** 数据点的平均强度（可在 `PL_onset_runner.py` 中改 `BASELINE_REGION`）。
5.  **切线搜索区**：主峰至高能端（`post_peak`），避免误选下降沿。
6.  结果同时给出 nm 与 eV；双谱模式额外报告 $\Delta\lambda$（flu − phos）与 $\Delta E$。

**使用方法**（请在 `TRPL/PL/` 目录运行）：

``` bash
# 弹窗选择单谱 / 双谱模式
python PL_onset_runner.py

# 单谱（命令行指定 CSV）
python PL_onset_runner.py "spectrum.csv"

# 荧光 + 磷光对比（两个 CSV）
python PL_onset_runner.py --dual "fluorescence.csv" "phosphorescence.csv"
```

**输出**（与输入 CSV 同目录，矢量图 **SVG**，600 dpi 渲染设置）：

| 模式 | 波长坐标图 | 能量坐标图 |
|------|------------|------------|
| 单谱 | `{stem}_onset.svg` | `{stem}_onset_energy.svg` |
| 双谱 | `{flu}_vs_{phos}_onset_diff.svg` | `{flu}_vs_{phos}_onset_diff_energy.svg` |

- **波长图**：横轴 nm，顶轴 eV；交互预览（`show_plot=True` 时弹出）。
- **能量图**：横轴 eV，顶轴 nm；内容与波长图一致（谱线、baseline、切线、onset），后台保存，不额外弹窗。
- **双谱图**：上下两 panel——原始 counts + 各自归一化 (0–1)；摘要框标注两条 onset 及 $\Delta\lambda$、$\Delta E$。

**默认参数**（`PL_onset_runner.py` 顶部常量）：`BASELINE_REGION = 0.10`，`WINDOW_LENGTH = 11`，`POLYORDER = 3`，`SAVE_DPI = 600`。

**图幅样式**：20 cm × 15 cm，Arial 20 pt，内向刻度，Y 轴科学计数法写入轴标题。

**相关模块**：`onset_calculator.py`（读文件 + 能量域 onset 计算）、`plotter.py`（波长 / 能量验证图与双谱对比图）、`filters.py`（Savitzky–Golay）。上次使用的数据目录记录在 `pl_onset_last_path.json`。

**依赖**：`numpy`、`matplotlib`；图形界面需 `tkinter`。

------------------------------------------------------------------------

## Python plot lib 文件夹 {#python-plot-lib-文件夹}

与光谱 / 阈值 / 衰减等**本地交互绘图**相关的脚本主要位于子目录 **`Python plot/`**（下列「清单与多曲线」「共享样式」等条目均指该路径下的文件）。

### 清单与多曲线（Join curves）

- **`Manifest_index.py`**：在对话框中选择数据根目录后递归扫描 `*.csv` / `*.txt` / `*.xlsx`，生成 **`plot_manifest.xlsx`**（列含 `File_Path`、`Rel_Path`、`Label`），为下游作图统一登记路径。
- **`Join_curves.py`**：读取清单后启动 **Dash** 网页界面，勾选多条曲线在同一图中对比；支持轴标题、配色、归一化、对数 Y、图例与矢量导出；配置写入同目录的 **`join_curves_config.json`**，确认后导出 **PDF/SVG**。运行方式：在 **`Python plot/`** 目录下执行 `python Join_curves.py`（以便正确导入 `PlotUtils` 等同级模块）。
- **`Join_curves_nonDash_core.py`**：**无 Dash 的核心模块**——清单解析（`parse_manifest`）、读数、组序列（`collect_series`）、Plotly 预览图与 Matplotlib 静态导出等均在此；由 `Join_curves.py` 引用，也可在其他脚本中单独 `import` 做批处理或二次开发。

### 共享样式与其他 Python 内嵌作图

- **`PlotUtils.py`**：Matplotlib 样式、`create_matched_fig_ax`、全局字号、多曲线调色板（`SPECTRA_COLOR_PALETTES`）等，供 Join_curves、Threshold_graph 等共用。
- **`Threshold_graph.py`**：ASE 阈值数据的 Plotly 预览与 Matplotlib 导出（交互模式与 Join_curves 类似）。
- **`Decay_graph.py`**：时间分辨衰减曲线作图（含 Time Scan 等逻辑）。
- **`Laser_graph.py`**：DFB 激光器相关多文件对比与出图。

### Origin 导出类脚本（文件名常含 `_origin`）

以下脚本用于**向 Origin 工作表写数据并调用 Origin 出图**，与上列在 **Python 内**用 Dash/Matplotlib 绘图的路线不同；若文件位于其他子目录，以实际路径为准。

- `Decay_graph_origin.py`：将衰减数据导入 Origin 并绘制带自动时间单位的图形。
- `Threshold_graph_origin.py`：绘制 ASE 阈值拟合结果的 Origin 图。
- `Laser_graph_origin.py`：针对 DFB 激光器数据的 Origin 可视化脚本。
- `Scan_decay.py`：这是 `Decay_graph_origin.py` 的批量版本，用于批量扫描并绘制衰减曲线的 Origin 图。**目前暂时不使用，未知稳定性**。