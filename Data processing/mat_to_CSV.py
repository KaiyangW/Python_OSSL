from pathlib import Path
import scipy.io as sio
import numpy as np

source_path = Path(r"C:\My files\Google drive sync\KIT\21_L21_Exp2_00_combined.mat")


def find_mat_files(path: Path):
    if path.is_file() and path.suffix.lower() == ".mat":
        return [path]
    if path.is_dir():
        return sorted(path.glob("*.mat"))
    raise FileNotFoundError(f"找不到 MAT 文件或文件夹: {path}")


def convert_mat_to_csv(mat_file: Path):
    mat_data = sio.loadmat(mat_file, squeeze_me=True, struct_as_record=False)

    try:
        ta_data = mat_data["TA_data"]
        times = np.asarray(ta_data.Stage_positions).reshape(-1)

        wl_cal = np.asarray(ta_data.wavelength_calibration)
        if wl_cal.ndim == 1:
            wavelengths = wl_cal.reshape(-1)
        elif wl_cal.ndim >= 2:
            # 兼容 MATLAB 1-based 第二行索引；若只有一行则回退到第一行
            wavelengths = wl_cal[1, :].reshape(-1) if wl_cal.shape[0] > 1 else wl_cal[0, :].reshape(-1)
        else:
            raise ValueError("wavelength_calibration 维度异常")

        dt_t = np.asarray(ta_data.dT_T)
    except (KeyError, AttributeError, IndexError) as exc:
        raise ValueError("未找到 TA_data.Stage_positions、TA_data.wavelength_calibration 或 TA_data.dT_T") from exc

    if dt_t.shape[0] != times.size:
        raise ValueError(f"dT_T 的行数({dt_t.shape[0]})和 times 数量({times.size})不一致")
    if dt_t.shape[1] != wavelengths.size:
        raise ValueError(f"dT_T 的列数({dt_t.shape[1]})和 wavelengths 数量({wavelengths.size})不一致")

    csv_matrix_core = np.column_stack((times, dt_t))
    top_row = np.concatenate(([0], wavelengths))
    csv_matrix = np.vstack((top_row, csv_matrix_core))

    # 和 MATLAB 里的 writematrix(csv_Matrix', ...) 保持一致。
    output_file = mat_file.with_suffix(".csv")
    np.savetxt(output_file, csv_matrix.T, delimiter="\t", fmt="%.8e")
    return output_file


if __name__ == "__main__":
    if not source_path.exists():
        print(f"Error: 路径不存在: {source_path}")
        raise SystemExit(1)

    for mat_file in find_mat_files(source_path):
        try:
            csv_file = convert_mat_to_csv(mat_file)
            print(f"Converted: {mat_file.name} -> {csv_file.name}")
        except Exception as e:
            print(f"Skipped {mat_file.name}: {e}")