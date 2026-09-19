# 参考代码

本工具独立运行，不需要把参考项目加入 PYTHONPATH，也不修改参考项目。

- `capture_tool/imv_binding.py`：摘取并适配自用户提供的
  `jsdfhasuh/emo_master`，分支 `agent/runtime-workflow-architecture-v1`，
  提交 `33fc53eca31978ca3be99b5e01b4863d84a2e482` 的
  `src/emo_master/plugins/builtins/_huaray_imv.py`。
  仅保留低层 ctypes 绑定；独立工具的会话、线程和参数应用由本项目实现。
- `capture_tool/imv_devices.py`：设备枚举 ABI 结构补充参考同工作区
  `calibration (2)/dahua_camera.py`，并核对
  `GreeVisionMaster20260818/.../camera_sdk/Dahua/MVSDK/IMVDefines.py`。
- 自动模式枚举和清理帧缓冲接口参考上述 SDK 的 `IMVApi.py`。
- 未将相机 DLL、驱动、MV Viewer 安装包纳入本项目。运行真机需要安装厂商运行库。
- Python、Qt/PySide6、NumPy、Pillow、pytest 遵循各自发行包的许可证。

参考分支的 `wait_plan/camera_live_tuning_plan.md` 是待实施设计文档，
本工具没有将该文档中的完整工作流编辑器、回滚或高级参数系统视为现成实现。
