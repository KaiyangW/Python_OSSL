# Data Processing 处理脚本集

> 最后更新：2026-06-07

## 📑 目录

- [ASE 文件夹](#ase-文件夹)
- [DFB devices 文件夹](#dfb-devices-文件夹)
- [KIT Transient Absorption 文件夹](#kit-transient-absorption-文件夹)
- [TRPL 文件夹](#trpl-文件夹)
  - [TRPL/PL 子文件夹](#trplpl-子文件夹)
  - [TRPL/TCSPC 子文件夹](#trpltcspc-子文件夹)
- [Python plot 文件夹](#python-plot-文件夹)
- [Python with origin 文件夹](#python-with-origin-文件夹)
- [根目录脚本](#根目录脚本)

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

### 5. `ASE_manual_fit.py`

**用途**：ASE / DFB 阈值数据的**手动分段线性拟合 GUI**。读取 `*_Analysed_ultra.xlsx` 中的入射能量–积分强度曲线，交互剔除异常点后拟合，确定阈值转折点。 **使用方法**（CustomTkinter 界面）：

1. 运行后点击 “Select Folder (Analysed_ultra)”，选择仅含一个 `*_Analysed_ultra.xlsx` 的文件夹。
2. 选择 2 / 3 / 4 段线性（即 1–3 个转折点）。
3. **左键**点击数据点 = 拟合与绘图都排除；**右键** = 仅拟合排除、绘图保留。
4. 实时查看转折点坐标及误差、各段斜率、斜率比、R²，确认后点击 “SAVE RESULTS”。

**输出**： - `ManualFit_Result_{base}.xlsx`（含 Parameters / Data_and_Mask / Plot_data / Fit_Line 工作表）。 - `{base}_ManualFit_Plot.png`。 - 若存在同名 `{base}_auto_fit.xlsx` 会被删除（手动结果优先）。 **说明**：独立运行，无 sibling 依赖。

------------------------------------------------------------------------

### 6. `Abs_percentage_TMM.py`

**用途**：基于**传输矩阵法（TMM）**计算薄膜吸收率 $A = 1 - R - T$。读取 CompleteEASE 导出的 $n$、$k$ 数据，按给定膜厚计算正入射下 $R$、$T$、$A$ 随波长的变化。 **模型**：三层结构 空气 ($n=1.0$) / 薄膜 / 玻璃基底 ($n=1.52$)，用 `tmm.unpolarized_RT` 逐波长计算；标注 330 nm 处吸收率及 >400 nm 区间吸收峰值。

**使用方法**： - 无参数运行：弹出文件选择与膜厚输入框。 - 命令行：`python Abs_percentage_TMM.py "path/to/nk data.txt" 120`（nk 文件 + 膜厚 nm）。

**输出**（与 nk 文件同目录）： - `{stem}_absorption_TMM.csv`（wavelength_nm, R, T, A_percent）。 - `{stem}_absorption_TMM.pdf`（600 dpi）。 **依赖**：`tmm`、`pandas`、`numpy`、`matplotlib`；记忆上次路径于 `abs_tmm_last_path.json`。

------------------------------------------------------------------------

### 7. `Oceanoptics data process.py`

**用途**：**Ocean Optics 光谱 CSV 批处理工具**。递归扫描文件夹，对 ASE 光谱做 PL 背景扣除、FWHM 与积分强度计算，并生成汇总图与 Excel（算法逻辑移植自 `ASE Threshold data processing ultra.py`，但为独立脚本）。 **关键逻辑**： - 波长裁剪 390–800 nm（脚本顶部可改）；排除文件名含 `background` 的文件。 - 自动检测峰位，积分窗口为 peak ± 10 nm。 - PL 参考取最弱 3 条谱线平均并 Savitzky‑Golay 平滑，缩放后在积分窗口外相减。 - FWHM 取多窗口 Savitzky‑Golay 平滑结果的中位数。

**使用方法**：运行后选根目录，递归处理各含 CSV 的子文件夹。 **输出**（每个处理文件夹内）： - `Results_{folder}.xlsx`。 - `Summary_Intensity_FWHM_{folder}.png`（双 Y 轴）。 - `All_Normalized_Spectra_{folder}.png`。

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

### 4. `DFB_FWHM_math_core.py`

**用途**：有机 DFB 激光光谱 FWHM 的**确定性计算库**（供其他脚本 import），专门解决阈值前噪声尖峰导致 FWHM 过早塌缩的问题。 **算法**： - 预处理：3 点中值滤波 + 宽 Savitzky‑Golay 包络。 - 残差（清洗信号 − 包络）提取窄带相干成分；同时满足拓扑判据（残差峰 prominence ≥ 包络峰值 10%）与能量判据（残差能量占比 ≥ 15%）时判为 “lasing”，在清洗信号上测 FWHM，否则在包络上测（宽 PL/ASE）。 - 亚像素半高宽：线性插值求半高交叉点。 **API**：`calculate_dfb_fwhm_adaptive(wavelength, intensity, ...)`，可选 `return_diagnostics=True`。 **说明**：纯模块，**无法独立运行**，仅依赖 numpy、scipy。

------------------------------------------------------------------------

### 5. `Get_pixel_area.py`

**用途**：分析 **BeamView** 导出的二维光束强度矩阵，按 **1/e²（13.5% 峰值）** 标准统计光束面积并出图。 **逻辑**：读取 CSV 头信息中的 `PixelWidth` / `PixelHeight`（µm），统计超阈值像素数并换算物理面积（µm² / mm² / cm²）。 **使用方法**：运行后选 BeamView 导出的 `.csv` / `.txt`。 **输出**（同目录）： - `{base}_profile_clean.svg`（无等值线）。 - `{base}_profile.svg` / `.pdf`（含红色 1/e² 等值线，600 dpi）。 - 控制台打印像素数与物理面积。

------------------------------------------------------------------------

### 6. `SE_cross_section.py`

**用途**：**受激发射（SE）截面计算器 GUI**。基于 Füchtbauer–Ladenburg 关系（Nakanotani et al., *Adv. Opt. Mater.* 2017），由 PL 谱、折射率 $n$ 与辐射寿命计算 $\sigma_{em}(\lambda)$。 **公式**：$\sigma_{em} = \lambda^4 E_f / (8\pi n^2 c\,\tau_{rad})$（CGS 单位，结果 cm²），其中 $\tau_{rad} = \tau_f / \mathrm{PLQY}$，PL 谱面积归一化得 $E_f$。 **使用方法**（CustomTkinter）：导入 PL 数据与 $n$ 数据，输入 $\tau_f$ (ns) 与 PLQY，点击 CALCULATE → SAVE RESULTS。 **输出**：CSV（Wavelength_nm, Ef_per_cm, n, sigma_em_cm2，含参数头注释），默认 `{PL_stem}_SE_cross_section.csv`。

------------------------------------------------------------------------

### 7. `Spectrum wavelength offset correction.py`

**用途**：批量**修正光谱 CSV 的波长轴偏移**。递归扫描文件夹，对文件名含 `spec` 的 CSV **原地**加上固定偏移量（nm）。 **配置项**（脚本顶部）：`WAVELENGTH_OFFSET`（默认 −1 nm）、`TARGET_FORMAT`（`both` / `standard` / `transpose`）、`BASE_FOLDER`。 **使用方法**：设置好常量后运行；`BASE_FOLDER` 留空则弹窗选根目录。排除文件名含 `extract` / `process` 的文件。 **输出**：**原地覆盖**匹配的 spec CSV（无新文件），控制台打印每个文件的格式与修正点数。 **注意**：会直接改写原始数据，请先备份。

------------------------------------------------------------------------

## KIT Transient Absorption 文件夹 {#kit-transient-absorption-文件夹}

### 1. `mat_to_CSV.py`

**用途**：批量将 KIT / LabVIEW 导出的瞬态吸收（TA）`.mat` 文件转为 CSV，便于后续 Excel 或 `TA_data_reading.py` 使用。 **特性**： - 读取 `TA_data` 结构体：`Stage_positions`（时间）、`wavelength_calibration`（波长）、`dT_T`（信号矩阵）。 - 兼容 MAT v7.3（`mat73`）、标准 v5（`scipy`）、非标准 v5（`mat4py`）。 - 检测 Google Drive / OneDrive 占位文件并提示先下载到本地。 - `ProcessPoolExecutor` 按文件夹并行批量转换。 **使用方法**：运行后选文件夹，递归扫描所有 `.mat`。 **输出**：每个 `.mat` 同目录生成同名 `.csv`（行=波长 nm，列=时间 s）。

------------------------------------------------------------------------

### 2. `TA_data_reading.py`

**用途**：KIT TA 数据的**交互式可视化工具**。加载二维矩阵（波长 × 时间）的 `.xlsx`，支持按时间切片出谱或按波长切片出动力学曲线。 **核心能力**： - 两种模式：`Time → spectrum`（固定时间出谱）、`Wavelength → trace`（固定波长出 trace）。 - 时间 / 波长方向可选滑动平均（10 ps / 1 ns / 2 ns；2 nm / 3 nm）。 - 多曲线叠加、鼠标悬停最近点读数、Matplotlib 工具栏缩放、Windows 高 DPI 自适应。 **使用方法**：运行后 Browse / Load 选择 `.xlsx`，设置参数后点击 **Add Curve**，**Clear Curves** 清空。 **输出**：无文件输出，仅屏幕交互图。

------------------------------------------------------------------------

## TRPL 文件夹 {#trpl-文件夹}

> 该文件夹分为两个子目录：`TRPL/PL`（稳态 / 门控 PL 与发射光谱分析）与 `TRPL/TCSPC`（时间相关单光子计数寿命拟合）。
>
> **注**：旧版的 `Auto_align_decay_data.py`、`Away_gated_diffraction.py`、独立 GUI `RISC_exact_solution.py`、`lifetime_fit.py` 等已移除或重命名（见下文）；TRPL 的批量预处理脚本 `Draw_mutiple_graphs.py` 与核心引擎 `trpl_processor.py` 现位于 [`Python plot/`](#python-plot-文件夹) 目录。

## TRPL/PL 子文件夹 {#trplpl-子文件夹}

### 1. `Voigt_PL.py`

**用途**：对稳态 PL 发射光谱做 **Voigt 轮廓拟合**，当前支持两种分析模式（启动时手动选择，或通过 `--mode` 指定）：

| 模式 | `--mode` | 说明 |
|--------------------|--------------------------------|--------------------|
| Gamma 敏感性扫描 | `sensitivity` | 全文件夹双 Voigt（0-0 + 0-1），扫描固定 $\gamma$ 下的 $\sigma$ |
| RT + 77K 全局联立 | `global` | 手动各选 RT、77K 光谱，共享 $\sigma$，独立 $\gamma$ |

> **说明**：旧版独立的 `77k-dual` 模式已从 `--mode` 选项中移除（其 77K 双峰逻辑现整合进 `global` 模式）；`--global-fit` 已弃用，等同 `--mode global`。

**数据预处理**（两种模式共用）：调用 `load_pl_csv` 读取 `.csv` 光谱（第一列波长 nm，第二列强度，兼容 Jasco 头），转换为能量坐标 $E = 1239.8 / \lambda$；强度按雅可比修正 $I_E = I_\lambda \cdot \lambda^2$。自动估计峰位与 FWHM 并在局部窗口内拟合。文件名含 `Gated` 的 CSV 自动跳过。基线固定为 0。

**使用方法**：

``` bash
# 弹窗选文件夹 + 选模式
python Voigt_PL.py "数据文件夹"

# 模式 1：gamma 敏感性扫描（全文件夹）
python Voigt_PL.py "数据文件夹" --mode sensitivity
python Voigt_PL.py "数据文件夹" --mode sensitivity --workers 4

# 模式 2：RT + 77K 全局联立
python Voigt_PL.py "数据文件夹" --mode global
python Voigt_PL.py "数据文件夹" --mode global --rt-csv "xxx_RT.csv" --77k-csv "xxx_77K.csv"
```

**依赖**：`numpy`、`pandas`、`matplotlib`、`lmfit`、`scipy`、`tqdm`；图形界面需 `tkinter`。出图与汇总导出由同目录 `Voigt_PL_output.py` 提供（见下）。

------------------------------------------------------------------------

#### 模式 1：`sensitivity`（Gamma 敏感性扫描）

**拟合逻辑**：双 Voigt（0-0 + 0-1，0-1 固定较 0-0 低约 0.15 eV）；对每个光谱在 $\gamma = 10\text{--}30\ \mathrm{meV}$（步长 2 meV）下固定 $\gamma$ 拟合，记录 $\sigma$、中心、$R^2$ 等。`ProcessPoolExecutor` 并行，`tqdm` 进度条。

**输出**（`Voigt Output/`）：

- `voigt_gamma_sensitivity_summary.xlsx`：每个 `(文件名, gamma)` 一行（**扫描表格式**）。
- `{样本名}_gamma20meV_fit.png`：$\gamma = 20\ \mathrm{meV}$ 拟合图（Voigt + 高斯/洛伦兹分解）。
- `sigma_vs_gamma_sensitivity.png` / `.svg`：$\sigma$–$\gamma$ 敏感性折线图。

------------------------------------------------------------------------

#### 模式 2：`global`（RT + 77K 全局联立）

**模型**：

- **RT**：单 Voigt。
- **77K**：双 Voigt（0-0 + 0-1），`center_01 = center_00 - E_\mathrm{vib}`，$E_\mathrm{vib}$ 约束在 **0.15–0.18 eV**（典型 C–C 骨架伸缩）。
- **共享** `sigma_global`（RT 与 77K 两峰共用）；`gamma_RT`、`gamma_77K` 独立，且 `gamma_77K < gamma_RT`。

**输出**（`Voigt Output/`）：

- `global_voigt_fit_summary.csv`：全局参数汇总。
- `global_RT_77K_voigt_fit.png`：上下两 panel（RT 单峰；77K 双峰 + 分量虚线）。

------------------------------------------------------------------------

#### 出图样式

- 图幅 20 cm × 15 cm，16 pt 字体；仅左、下轴刻度（上、右无刻度）。
- 敏感性折线图例置于绘图区右侧外部，`savefig(..., bbox_inches='tight')` 防止裁切。

------------------------------------------------------------------------

### 2. `Voigt_PL_output.py`

**用途**：`Voigt_PL.py` 的**出图与参数导出工具库**（非主程序，**无法独立运行**）。统一期刊风格绘图，并把拟合摘要写入 CSV / XLSX。 **核心函数**： - `apply_plot_style`：20×15 cm、16 pt、上右无刻度、无网格。 - `plot_representative_fit_77k_dual` / `plot_representative_fit`：77K 0-0 / 0-1 双 Voigt 分量 + 总拟合。 - `plot_global_fit_comparison`：RT 与 77K 全局联立拟合对比图。 - `plot_sigma_sensitivity`：固定 $\gamma$ 下 $\sigma$ 敏感性折线图。 - `save_global_fit_summary` / `save_77k_dual_fit_summary` / `save_sensitivity_summary_xlsx`：写出参数/汇总表。 **说明**：被 `Voigt_PL.py` import 并注入回调；输出默认写入 `Voigt Output/`（由 `ensure_output_dir` 创建）。

------------------------------------------------------------------------

### 3. `Remove_Peak.py`

**用途**：针对 **136 ps gated PL、80 K** 光谱的处理脚本——用 **RT PL 模板**减去 prompt fluorescence（PF），提取干净的 phosphorescence 谱。 **算法**： - 自动识别 CSV 前两列数值（多编码/多分隔符），可选 dark 扣除与 420–450 nm 基线校正。 - 波长 ↔ 能量域 Jacobian 转换（1240 eV·nm）。 - 几何边缘匹配：调整 RT 模板的 shift（eV）与 gamma 展宽，匹配 80 K PF 蓝边半高与 1/e 交叉点；在 PF 蓝边窗口（2.620–2.780 eV）最小二乘求缩放因子 $\alpha$。 - 余弦 taper 权重：PF 峰以下渐变停止扣除，避免伤及磷光区；负值裁剪为 0。

**使用方法**： - 命令行：`python Remove_Peak.py [80K.csv] [RT.csv] [--dark-80k ...] [--dark-rt ...] [--manual-alpha/shift/gamma ...]` 等。 - 未给路径时弹窗依次选 80 K 与 RT 谱。 **输出**（基于 80K 文件名）： - `*_phosphorescence_fit_table.csv`。 - `*_phosphorescence_fit_plot.png`（能量域）、`*_phosphorescence_fit_plot_lambda.png`（波长域）。 - `*_phosphorescence_fit_params.json`，可选 `*_clean_phosphorescence_lambda.csv`。

------------------------------------------------------------------------

### 4. `PL_onset_runner.py`（PL 起峰 / onset 检测）

**路径**：`TRPL/PL/`（同目录含 `onset_calculator.py`、`plotter.py`、`filters.py`）。

**用途**：从稳态 PL 发射光谱（Jasco 等导出的 CSV/TXT）自动检测 **起峰波长 / 能量**，并生成带 baseline、切线与 onset 标记的验证图。支持单谱分析与荧光 / 磷光双谱对比。

**算法（Tangent–Baseline Intersection，能量域计算）**：

1.  读取 CSV 中前两列数值行（默认：波长 nm + 强度 counts），忽略文件头元数据。
2.  将 $I(\lambda)$ 转换为光子能量坐标，并做雅可比修正： $$I(E) = I(\lambda)\,\frac{\lambda^2}{hc},\qquad hc = 1240\ \mathrm{eV{\cdot}nm}$$
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
|-----------------|----------------------------|----------------------------|
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

## TRPL/TCSPC 子文件夹 {#trpltcspc-子文件夹}

> 该子目录是**时间相关单光子计数（TCSPC）寿命拟合栈**。统一主程序为 `mainUI_lifetime_fit.py`，其余为它编排（orchestrate）的引擎与工具模块。

### 1. `mainUI_lifetime_fit.py`（主程序 GUI）

**用途**：**统一的 TRPL 寿命拟合交互式主程序（GUI）**，整合 Reconvolution Fit 与 Tail Fit（即旧版 `lifetime_fit.py` 的演进 / 重命名版本）。 **核心能力**： - **双模式切换**：启动时选择反卷积拟合（Reconvolution）或尾部拟合（Tail Fit）。 - **智能 IRF 管理**：反卷积模式下自动搜索并匹配 IRF（以 `IRF` 命名的 CSV）并对齐上升沿。 - **功能全集成**：参数配置、区间（xmin/xmax）调整、最多 4 分量 $\tau/\beta$（可固定）、磷光标记、Area Diff（PF/DF）、外推 / Scatter 选项、PLQY 与 PF/DF 分量选择、**动力学速率分析（RISC）** 开关、JSON 配置存取。 - **参数导出一致性**：同时存入 UI 显示的 $\tau_{eff}$ 与动力学专用的 $\tau_{mean}$。

**使用方法**：`python mainUI_lifetime_fit.py` 或 `python mainUI_lifetime_fit.py path/to/decay.csv` → 选模式 → 选 CSV（Recon 还需 IRF）→ Open Fit Menu → Run Fit → Save Results。 **输出**：用户指定 `*.xlsx`（Parameters + Fit_Curve 两个工作表）+ 同路径 `*.png`（400 dpi）；运行时写 `last_paths.json`。

**调用链概览**：

```
mainUI_lifetime_fit.py
  ├─ Recon_fit_process / Tail_fit_process      （拟合引擎）
  │    ├─ fit_multistart        （并行多起点 least_squares）
  │    ├─ fit_uncertainty       （JᵀJ 协方差 + delta method 误差）
  │    ├─ Area_Analysis_Engine  （PF/DF 面积差法）
  │    ├─ params_export         （Excel 参数行组装）
  │    └─ risc_calculator_bridge（可选 RISC 计算 + 面板文本）
  └─ risc_calculator_bridge      （左栏参数摘要面板布局）
```

------------------------------------------------------------------------

### 2. `Recon_fit_process.py`

**用途**：`mainUI_lifetime_fit.py` 反卷积拟合的核心数学引擎，负责底层物理计算。 **核心能力**： - **反卷积算法**：FFT 卷积将仪器响应函数（IRF）与衰减模型结合，最小二乘法精确拟合 TCSPC 数据。 - **伸缩指数物理模型**：多组分 Stretched Exponential 拟合，自动计算平均寿命 ($\langle \tau \rangle$)。 - **加权平均寿命**：光强加权 $\tau_{eff}$（UI 显示）与数量加权 $\tau_{mean}$（动力学速率输入，以 `Hidden_Number_Mean_Avg_Tau` 导出）。 - **PF/DF 定量**：内置“面积差值法”分离 PF / DF / 磷光比例（调用 `Area_Analysis_Engine`）。 **说明**：**无法独立运行**，由主程序调用。

------------------------------------------------------------------------

### 3. `Tail_fit_process.py`

**用途**：`mainUI_lifetime_fit.py` 尾部拟合的核心数学引擎。 **核心能力**： - **纯指数 / 伸缩指数拟合**：对衰减曲线尾部多组分拟合，无需 IRF 卷积。 - **逻辑对齐**：与 Recon 版本一致的平均寿命分析（$\tau_{eff}$ 用于 UI，$\tau_{mean}$ 用于动力学）与 PF/DF 比例计算。 **说明**：**无法独立运行**，由主程序调用。

------------------------------------------------------------------------

### 4. `Area_Analysis_Engine.py`

**用途**：从 stretched-exponential 拟合分量计算 **PF / DF 面积**的独立物理模块，供 Recon / Tail 两种路径共用。 **算法**： - `calc_recon_difference_areas`：Recon 面积差法 `PF = 原始积分 − DF − Phos [− Scatter]`，无需外推（拟合窗已含 IRF 上升沿）。 - `calc_tail_difference_areas`：Tail 面积差法，可选几何外推补偿（用 C1 的 $\tau_1/\beta_1$ 纯形状面积比修正 xmin 之前缺失的 PF）。 **说明**：纯计算模块，返回 dict、不写文件；勾选 “Enable Area Diff (PF/DF Ratio)” 时由引擎调用。

------------------------------------------------------------------------

### 5. `fit_multistart.py`

**用途**：寿命拟合的**多起点并行优化包装器**——在不改变残差 / 模型数学的前提下，从多组 $\tau/\beta$ 初值并行跑 `least_squares`，取最低 reduced $\chi^2$ 的结果。 **特性**：围绕用户初值对数扫描 $\tau$ 与 $\beta$ seed（用户精确初值恒为 start #0），最多 8 组起点，`ProcessPoolExecutor` 持久进程池（Windows spawn 友好）。 **说明**：**无法独立运行**，依赖 `Recon_fit_process` / `Tail_fit_process`，由其 `run_fitting_process` 调用。

------------------------------------------------------------------------

### 6. `fit_uncertainty.py`

**用途**：基于 **Poisson 加权残差**的参数不确定度估计，并对 derived lifetime 做 **delta method** 误差传播。 **算法**：TCSPC 计数按 Poisson 取权重 $1/\sqrt{\max(N,1)}$；在最优解处用 Jacobian 估计 $\mathrm{Cov}(p) \approx (J^\top J)^{-1}$；将误差传播到 intensity / number averaged $\tau$。提供 `format_val_err` / `format_beta_err` 供 UI 显示。 **说明**：库模块，由 Recon / Tail 引擎在优化完成后调用。

------------------------------------------------------------------------

### 7. `params_export.py`

**用途**：构建导出到 Excel「Parameters」工作表的参数行列表，尤其是 **RISC Calculator** 的输入 / 输出文档块。 **核心函数**：`append_section`（模块间空行分隔）、`build_risc_excel_input_rows`（$\tau_p$、$\tau_d$、$\Phi_{PF}$、$\Phi_{PLQY}$ 等输入）、`build_risc_approx_output_rows`（Masui / Dias / Wada 近似 $k_{RISC}$）。 **说明**：库模块，返回内存中的行列表，本身不写盘。

------------------------------------------------------------------------

### 8. `risc_calculator_bridge.py`

**用途**：通过 **xlwings** 驱动同目录 `RISC Calculator.xlsx`（只读打开、不保存），计算 TADF 精确 / 近似 RISC 速率，并提供主 GUI 左栏的参数摘要面板布局。 **核心能力**： - 写入 D5（$\tau_p$ ns）、D8（$\tau_d$ μs）、D11（$\Phi_{PF}$）、D13（$\Phi_{PLQY}$），迭代求 $k_{ISC}$，读取精确解与近似值。 - `compute_risc_rates` / `compute_risc_rates_batch`（Monte Carlo 批量）。 - `create_lifetime_fit_figure` / `FitSummaryPanel`：左栏彩色参数摘要。 **使用方法**：主流程勾选 “Compute RISC Rates (Excel)” 自动调用；亦可 `python risc_calculator_bridge.py` 跑内置示例。 **硬依赖**：同目录 `RISC Calculator.xlsx`、`xlwings` 与本机 Microsoft Excel。

------------------------------------------------------------------------

### 9. `RISC_exact_solution_coremath.py`

**用途**：TADF 动力学速率的**纯 Python 精确解析解引擎**（基于 Cardano 公式的三次方程求解），作为 Excel 桥接之外的并行 / 验证途径。 **核心功能**： - 从实验数据提取 $k_{RISC}$、$k_{ISC}$、$k_r$、$k_{nr}$ 等；相比近似公式在延迟分量比例较高时更精确。 - 严格要求输入**数量加权寿命** ($\tau_{mean}$)，以符合速率常数的物理定义。 - 自动校正 DF 分量，计算 $\Phi_{PF}$、$\Phi_{DE}$、$\Phi_{DF}$ 等。 **说明**：当前主流程主要走 `risc_calculator_bridge.py`（Excel）路径，本模块作为独立精确解共存，可单独 import / 运行做验证。原独立 GUI `RISC_exact_solution.py` 已移除。

------------------------------------------------------------------------

## Python plot 文件夹 {#python-plot-文件夹}

与光谱 / 阈值 / 衰减等**本地交互绘图**及 TRPL 批量预处理相关的脚本位于子目录 **`Python plot/`**。

### 清单与多曲线（Join curves）

- **`Manifest_index.py`**：在对话框中选择数据根目录后递归扫描 `*.csv` / `*.txt` / `*.xlsx`，生成 **`plot_manifest.xlsx`**（列含 `File_Path`、`Rel_Path`、`Label`），为下游作图统一登记路径。
- **`Join_curves.py`**：读取清单后启动 **Dash** 网页界面，勾选多条曲线在同一图中对比；支持轴标题、配色、归一化、对数 Y、图例与矢量导出；配置写入同目录的 **`join_curves_config.json`**，确认后导出 **PDF/SVG**。运行方式：在 **`Python plot/`** 目录下执行 `python Join_curves.py`（以便正确导入 `PlotUtils` 等同级模块）。
- **`Join_curves_nonDash_core.py`**：**无 Dash 的核心模块**——清单解析（`parse_manifest`）、读数、组序列（`collect_series`）、Plotly 预览图与 Matplotlib 静态导出等均在此；由 `Join_curves.py` 引用，也可在其他脚本中单独 `import` 做批处理或二次开发。

### 共享样式与其他 Python 内嵌作图

- **`PlotUtils.py`**：Matplotlib 样式、`create_matched_fig_ax`、全局字号、多曲线调色板（`SPECTRA_COLOR_PALETTES`）、`DynamicPlotExplorer` 等，供 Join_curves、ASE_graph 等共用。
- **`ASE_graph.py`**：ASE 阈值数据的 Plotly 预览与 Matplotlib 导出（交互模式与 Join_curves 类似，原 `Threshold_graph.py` 的后继 / 重命名版本）。在阈值图基础上**新增** `Analysed_ultra` 光谱演化图。导出 `Threshold_graph.pdf/.svg`，有光谱数据时另出 `Spectral_Evolution.pdf/.svg`；配置存于 `threshold_plot_config.json`。**依赖**同目录 `PlotUtils.py`。
- **`Decay_graph.py`**：时间分辨衰减曲线作图（含 Time Scan 等逻辑）。
- **`Laser_graph.py`**：DFB 激光器相关多文件对比与出图。

### TRPL 批量预处理（位于 `Python plot/`）

- **`Draw_mutiple_graphs.py`**（注意拼写为 *mutiple*）：批量处理大量 TRPL / PL / UV-Vis 数据的进阶脚本。递归扫描文件夹内所有 Jasco `.csv` / `.txt`，**按子文件夹分组多进程并行**。TRPL：以该文件夹内**最晚上升沿**为基准对齐并将基线归一到 1；PL（emission scan）：寻峰、计算 FWHM 与积分强度并写入标题；UV-Vis：标注 330 nm 与 ≥400 nm 吸收。为每个源文件旁生成同名 `.png`（400 dpi）。**依赖**同目录 `trpl_processor.py`。
- **`trpl_processor.py`**：TRPL 数据的对齐 / 归一化核心引擎（含 `align_baseline_to_one` 等）。**无法独立运行**，是 `Draw_mutiple_graphs.py` 的底层数学引擎。

------------------------------------------------------------------------

## Python with origin 文件夹 {#python-with-origin-文件夹}

以下脚本用于**向 Origin 工作表写数据并调用 Origin 出图**，与 `Python plot/` 里在 **Python 内**用 Dash / Matplotlib 绘图的路线不同。模板文件位于 `Python with origin/origin_template/`。

- `Decay_graph_origin.py`：将衰减数据导入 Origin 并绘制带自动时间单位的图形。
- `Threshold_graph_origin.py`：绘制 ASE 阈值拟合结果的 Origin 图。
- `Laser_graph_origin.py`：针对 DFB 激光器数据的 Origin 可视化脚本。
- `Scan_decay.py`：`Decay_graph_origin.py` 的批量版本，用于批量扫描并绘制衰减曲线的 Origin 图。**目前暂时不使用，未知稳定性**。

------------------------------------------------------------------------

## 根目录脚本 {#根目录脚本}

### `Save any pic.py`

**用途**：通过 GUI 输入自定义文字，生成一张高分辨率的「文件夹说明 / 警示」图片 PNG，用于在资源管理器中标记文件夹状态（如 “Data invalid due to low signal”）。 **使用方法**：运行后在输入框填写说明文字，选择保存位置（默认 `IMPORTANT_NOTE.png`）。 **输出**：600 DPI、深紫底白字、自动换行居中的 PNG。 **依赖**：`tkinter` + `Pillow`，独立运行。