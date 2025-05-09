import bpy
import os
import json
import re
import mathutils
from pathlib import Path
import urllib.request
import ssl
import threading
import tempfile
import shutil
import time
import math
import random
from urllib.error import URLError, HTTPError
import ssl
import json
#DEF URL127001
SERVER_URL = "http://124.71.76.131:8012"

bl_info = {
    "name": "骨骼动画管理工具",
    "author": "ΔIng KMnO4",
    "version": (1, 10, 6),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Animation",
    "description": "服务器复活了! 骨骼动画管理工具 Dev 1.9.4 what's new:插件更新地址已订到:http://blenderplusinupdata.432445.xyz有效期一年 有BUG请反馈 bug@432445.xyz",
    "warning": "",
    "doc_url": "",
    "category": "Animation",
}
LIBRARY_PATH = Path.home() / "AniTools"
LIBRARY_PATH.mkdir(parents=True, exist_ok=True)

# 跟踪启动定时器是否注册
_startup_timer_registered = False

# 服务器设置
UPDATE_SERVER = "http://blenderplusinupdata.432445.xyz"
UPDATE_CHECK_URL = f"{UPDATE_SERVER}/check_update"
UPDATE_CHECK_INTERVAL = 86400
last_update_check = 0
update_available = False
latest_version = None
download_url = None
update_message = ""
# 存储在线动作项的类
class OnlineActionItem(bpy.types.PropertyGroup):
    """存储从服务器获取的单个动作信息"""
    id: bpy.props.IntProperty()
    name: bpy.props.StringProperty(name="动作名称")
    description: bpy.props.StringProperty(name="描述")
    author: bpy.props.StringProperty(name="作者")
    timestamp: bpy.props.StringProperty(name="上传时间")
    download_url: bpy.props.StringProperty(name="下载链接")
def clean_filename(name):
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip('_')[:120]

def format_vector(v, prec=3):
    return f"({v.x:.{prec}f}, {v.y:.{prec}f}, {v.z:.{prec}f})"

class ActionLibraryItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="动作名称")
    filepath: bpy.props.StringProperty(name="文件路径")
    source_fbx: bpy.props.StringProperty(name="来源FBX")
    frame_range: bpy.props.IntVectorProperty(size=2, name="帧范围")

def get_target_bones(scene, context):

    items = []
    
    # 当前选中骨架
    active_obj = context.active_object
    if active_obj and active_obj.type == 'ARMATURE':
        # 添加一个空选项，表示不映射
        items.append(("", "不映射", "不映射到任何骨骼"))
        
        # 获取骨架中的所有骨骼
        for bone in active_obj.data.bones:
            items.append((bone.name, bone.name, f"映射到 {bone.name} 骨骼"))
    
    # 如果没有找到骨架或骨骼，显示一个提示选项
    if not items:
        items.append(("", "请先选择骨架", ""))
    
    return items

def get_target_bones_enum(self, context):

    items = []
    
    # 当前选中骨架
    active_obj = context.active_object
    if active_obj and active_obj.type == 'ARMATURE':
        # 添加一个空选项，表示不映射
        items.append(("", "不映射", "不映射到任何骨骼"))
        
        # 获取骨架中的所有骨骼
        for bone in active_obj.data.bones:
            items.append((bone.name, bone.name, f"映射到 {bone.name} 骨骼"))
    
    # 如果没有找到骨架或骨骼，显示一个提示选项
    if not items:
        items.append(("", "请先选择骨架", ""))
    
    return items

def get_bone_search_items(self, context):

    items = []
    
    # 获取当前骨架
    active_obj = context.active_object
    if active_obj and active_obj.type == 'ARMATURE':
        # 获取搜索文本
        search_text = self.bone_search_text.lower() if hasattr(self, 'bone_search_text') else ""
        
        # 搜索所有匹配的骨骼
        bone_names = []
        for bone in active_obj.data.bones:
            if search_text in bone.name.lower():
                bone_names.append(bone.name)
        
        # 按字母排序
        bone_names.sort()
        
        # 转换为选项列表
        for name in bone_names:
            items.append((name, name, f"选择 {name}"))
    
    # 如果没有匹配的骨骼，添加一个提示
    if not items:
        items.append(("", "没有匹配的骨骼", ""))
        
    return items

class BoneSelectionItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="骨骼名称")
    selected: bpy.props.BoolProperty(name="选中", default=True)
    mapped_to: bpy.props.StringProperty(name="映射到", default="")
    use_mapping: bpy.props.BoolProperty(name="启用映射", default=False)

class AnimMergeProperties(bpy.types.PropertyGroup):
    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    source_start: bpy.props.IntProperty(
        name="源起始帧",
        default=1,
        min=0
    )
    source_end: bpy.props.IntProperty(
        name="源结束帧",
        default=250,
        min=1
    )
    target_start: bpy.props.IntProperty(
        name="目标起始帧",
        default=1,
        min=0
    )
    loop_times: bpy.props.IntProperty(
        name="循环次数",
        default=1,
        min=1,
        description="将动画循环复制指定次数"
    )
    merge_mode: bpy.props.EnumProperty(
        name="合并模式",
        items=[
            ('REPLACE', "覆盖", "完全替换目标时间段内的动画"),
            ('MIX', "混合(测试ing)", "保留原有动画并混合新动画")
        ],
        default='REPLACE'
    )
    apply_transform: bpy.props.BoolProperty(
        name="保持骨架位置和旋转",
        default=True,
        description="应用动画时保持原有骨架的位置和旋转"
    )
    action_library_index: bpy.props.IntProperty(default=0)
    action_library_items: bpy.props.CollectionProperty(type=ActionLibraryItem)
    show_library: bpy.props.BoolProperty(default=False, name="显示动作库")
    is_from_library: bpy.props.BoolProperty(default=False)
    
    # 自动检查更新设置
    auto_check_update: bpy.props.BoolProperty(
        name="自动检查更新",
        default=True,
        description="启动时自动检查插件更新"
    )
    
    # 添加骨骼映射控制
    enable_bone_mapping: bpy.props.BoolProperty(
        name="启用骨骼映射",
        default=False,
        description="允许将源动画中的骨骼映射到目标骨架中的骨骼",
        update=lambda self, context: update_bone_mapping(self, context)
    )
    
    show_mapping_help: bpy.props.BoolProperty(
        name="显示帮助",
        default=False
    )
    
    # 添加当前活动面板属性
    active_panel: bpy.props.EnumProperty(
        name="当前功能",
        items=[
            ('MERGE', "动画合并", "FBX动画合并功能"),
            ('LIBRARY', "动作库", "动作资产库功能"),
            ('PATH', "路径动画", "贝塞尔路径动画工具"),
            ('BREATH', "呼吸动画", "骨骼呼吸动画功能"),
            ('CAMERA', "摄像机动画", "摄像机相关动画工具"), # 新增摄像机功能选项
            ('UPDATE', "更新信息", "插件更新与信息"),
            ('ONLINE', "在线动作库", "浏览和下载网络动作库")  # 新增选项
        ],
        default='MERGE'
    )
    
    # 在线动作库设置
    server_url: bpy.props.StringProperty(
        name="服务器地址",
        description="在线动作库服务器URL",
        default=SERVER_URL
    )
    online_actions: bpy.props.CollectionProperty(type=OnlineActionItem)
    online_action_index: bpy.props.IntProperty(default=0)
    is_loading_actions: bpy.props.BoolProperty(default=False)

class PathAnimationProperties(bpy.types.PropertyGroup):
    """贝塞尔曲线路径动画设置"""
    
    # 曲线控制点数量
    control_points: bpy.props.IntProperty(
        name="控制点数量",
        description="贝塞尔曲线控制点数量",
        default=3,
        min=2,
        max=20
    )
    
    # 存储上一次应用的控制点数量
    applied_control_points: bpy.props.IntProperty(default=3)
    
    # 动画帧范围
    frame_start: bpy.props.IntProperty(
        name="起始帧",
        description="路径动画的起始帧",
        default=1,
        min=1
    )
    
    frame_end: bpy.props.IntProperty(
        name="结束帧",
        description="路径动画的结束帧",
        default=250,
        min=2
    )
    
    # 速度设置
    speed_factor: bpy.props.FloatProperty(
        name="速度因子",
        description="控制物体沿路径移动的速度",
        default=1.0,
        min=0.1,
        max=10.0
    )
    
    # 插值类型 - 简化为三种选项
    interpolation_type: bpy.props.EnumProperty(
        name="插值类型",
        description="选择动画的插值方式",
        items=[
            ('LINEAR', "平移", "线性插值，均匀速度"),
            ('EASE_IN', "先慢后快", "物体会先慢慢加速，然后快速移动"),
            ('EASE_OUT', "先快后慢", "物体会先快速移动，然后慢慢减速")
        ],
        default='LINEAR'
    )
    
    # 是否旋转
    follow_path: bpy.props.BoolProperty(
        name="跟随路径旋转",
        description="物体移动时是否沿路径方向旋转",
        default=True
    )
    
    # 当前曲线对象
    curve_object: bpy.props.PointerProperty(
        type=bpy.types.Object,
        name="曲线对象",
        description="当前创建的贝塞尔曲线对象"
    )
    
    # 是否展示高级选项
    show_advanced: bpy.props.BoolProperty(
        name="高级选项",
        description="显示更多设置选项",
        default=False
    )
    
    # 循环类型
    loop_type: bpy.props.EnumProperty(
        name="循环类型",
        description="选择动画如何循环",
        items=[
            ('NONE', "不循环", "动画播放一次后停止"),
            ('REPEAT', "重复", "动画重复播放"),
            ('PING_PONG', "来回", "动画正向播放后再反向播放")
        ],
        default='NONE'
    )
    
    # 循环次数
    loop_count: bpy.props.IntProperty(
        name="循环次数",
        description="设置动画循环的次数 (0表示无限循环)",
        default=0,
        min=0,
        max=100
    )
    
    # 循环间隔
    loop_gap: bpy.props.IntProperty(
        name="循环间隔",
        description="设置循环之间的帧间隔 (仅用于重复模式)",
        default=0,
        min=0,
        max=100
    )
    
    # 是否更新时间线范围
    update_timeline: bpy.props.BoolProperty(
        name="更新时间线范围",
        description="应用路径动画时是否自动更新场景的时间线范围",
        default=False
    )

# 骨骼映射开关更新回调函数
def update_bone_mapping(self, context):
    # 如果对话框已经打开，不处理此回调，避免与对话框中的操作冲突
    if hasattr(context.window_manager, 'invoke_props_dialog_running') and context.window_manager.invoke_props_dialog_running:
        return
    
    # 保存当前状态
    context.scene['bone_mapping_enabled'] = self.enable_bone_mapping
    
    # 自动重新加载骨骼列表
    if hasattr(context.scene, 'bone_selection'):
        # 清空当前骨骼选择列表
        context.scene.bone_selection.clear()
        
        # 根据映射开关状态加载不同的骨骼列表
        if self.enable_bone_mapping:
            # 加载源文件中的骨骼列表
            source_bones = load_source_bones(context, self)
            
            # 如果成功获取到源骨骼，添加到选择列表
            if source_bones:
                for bone_name in sorted(source_bones):
                    item = context.scene.bone_selection.add()
                    item.name = bone_name
                    item.selected = True
                    item.use_mapping = False
                    item.mapped_to = ""
                print(f"自动加载了 {len(source_bones)} 个源骨骼")
            else:
                # 如果没有源骨骼，使用当前骨架的骨骼
                print("未找到源文件骨骼，使用当前骨架骨骼")
                active_obj = context.active_object
                if active_obj and active_obj.type == 'ARMATURE':
                    for bone in active_obj.data.bones:
                        item = context.scene.bone_selection.add()
                        item.name = bone.name
                        item.selected = True
                        item.use_mapping = False
                        item.mapped_to = ""
        else:
            # 不启用骨骼映射时，使用当前骨架的骨骼
            active_obj = context.active_object
            if active_obj and active_obj.type == 'ARMATURE':
                for bone in active_obj.data.bones:
                    item = context.scene.bone_selection.add()
                    item.name = bone.name
                    item.selected = True
                    item.use_mapping = False
                    item.mapped_to = ""
    
    return None

# 用于加载源文件骨骼的函数，便于重用
def load_source_bones(context, props):
    # 从文件中获取源骨骼
    source_bones = []
    
    # 根据来源类型(库或FBX文件)获取骨骼列表
    if props.is_from_library:
        if props.action_library_items and props.action_library_index < len(props.action_library_items):
            item = props.action_library_items[props.action_library_index]
            filepath = Path(item.filepath)
            
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    action_data = json.load(f)
                
                # 提取骨骼名称
                for fcurve in action_data.get('fcurves', []):
                    data_path = fcurve.get('data_path', '')
                    match = re.search(r'pose\.bones\["([^"]+)"\]', data_path)
                    if match and match.group(1) not in source_bones:
                        source_bones.append(match.group(1))
            except Exception as e:
                print(f"读取动作文件失败: {str(e)}")
    else:
        # 从FBX文件中获取骨骼
        if props.filepath:
            # 临时导入FBX以获取骨骼列表
            original_objects = set(context.scene.objects)
            try:
                bpy.ops.import_scene.fbx(
                    filepath=props.filepath,
                    use_anim=True,
                    automatic_bone_orientation=True,
                    ignore_leaf_bones=True
                )
                
                # 找出新导入的物体
                new_objects = set(context.scene.objects) - original_objects
                armatures = [obj for obj in new_objects if obj.type == 'ARMATURE']
                
                if armatures:
                    # 第一个骨架
                    armature = armatures[0]
                    # 获取骨骼名称
                    source_bones = [bone.name for bone in armature.data.bones]
                
                # 清理导入的物体
                for obj in new_objects:
                    bpy.data.objects.remove(obj, do_unlink=True)
            except Exception as e:
                print(f"FBX导入失败: {str(e)}")
    
    return source_bones

# 用于重新加载骨骼列表的操作符
class ANIM_OT_ReloadBoneMapping(bpy.types.Operator):

    bl_idname = "anim.reload_bone_mapping"
    bl_label = "重新加载骨骼"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        props = context.scene.anim_merge_props
        
        # 清空骨骼选择列表
        context.scene.bone_selection.clear()
        
        # 如果启用骨骼映射，获取源文件中的骨骼列表
        if props.enable_bone_mapping:
            # 加载源文件骨骼
            source_bones = load_source_bones(context, props)
            
            # 如果成功获取到源骨骼，添加到选择列表
            if source_bones:
                for bone_name in sorted(source_bones):
                    item = context.scene.bone_selection.add()
                    item.name = bone_name
                    item.selected = True
                    # 默认不启用映射
                    item.use_mapping = False
                    item.mapped_to = ""
                
                self.report({'INFO'}, f"已加载 {len(source_bones)} 个源骨骼")
            else:
                # 如果没有获取到源骨骼，使用当前骨架的骨骼
                self.report({'WARNING'}, "未找到源文件中的骨骼，将使用当前骨架的骨骼")
                active_obj = context.active_object
                if active_obj and active_obj.type == 'ARMATURE':
                    for bone in active_obj.data.bones:
                        item = context.scene.bone_selection.add()
                        item.name = bone.name
                        item.selected = True
                        item.use_mapping = False
                        item.mapped_to = ""
        else:
            # 不启用骨骼映射时，使用当前骨架的骨骼
            active_obj = context.active_object
            if active_obj and active_obj.type == 'ARMATURE':
                for bone in active_obj.data.bones:
                    item = context.scene.bone_selection.add()
                    item.name = bone.name
                    item.selected = True
                    item.use_mapping = False
                    item.mapped_to = ""
        
        return {'FINISHED'}

def load_fbx_animation(filepath):
    original_objects = set(bpy.context.scene.objects)
    try:
        bpy.ops.import_scene.fbx(
            filepath=filepath,
            use_anim=True,
            automatic_bone_orientation=True,
            ignore_leaf_bones=True
        )
    except Exception as e:
        print(f"FBX导入失败: {str(e)}")
        return None
    new_objects = set(bpy.context.scene.objects) - original_objects
    armatures = [obj for obj in new_objects if obj.type == 'ARMATURE']
    if not armatures:
        return None
    armature = armatures[0]
    action = armature.animation_data.action if armature.animation_data else None
    for obj in new_objects:
        if obj != armature:
            bpy.data.objects.remove(obj, do_unlink=True)
    return action

def serialize_action(action, save_path):
    action_data = {
        "name": action.name,
        "fcurves": []
    }
    for fcurve in action.fcurves:
        curve_data = {
            "data_path": fcurve.data_path,
            "array_index": fcurve.array_index,
            "keyframes": []
        }
        for kp in fcurve.keyframe_points:
            curve_data["keyframes"].append({
                "frame": kp.co.x,
                "value": kp.co.y,
                "interpolation": kp.interpolation
            })
        action_data["fcurves"].append(curve_data)
    
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(action_data, f, indent=2, ensure_ascii=False)

def deserialize_action(load_path):
    try:
        with open(load_path, 'r', encoding='utf-8') as f:
            action_data = json.load(f)
    except Exception as e:
        print(f"加载失败:{str(e)}")
        return None
    
    action = bpy.data.actions.new(name=action_data["name"])
    
    for curve_data in action_data["fcurves"]:
        fcurve = action.fcurves.new(
            curve_data["data_path"], 
            index=curve_data["array_index"]
        )
        for kp_data in curve_data["keyframes"]:
            kp = fcurve.keyframe_points.insert(kp_data["frame"], kp_data["value"])
            kp.interpolation = kp_data["interpolation"]
    
    return action

def merge_actions(target_action, source_action, time_offset, source_range, merge_mode, loop_times=1):
    # 确保参数为整数
    time_offset = int(time_offset)
    source_range = (int(source_range[0]), int(source_range[1]))
    
    merged_action = target_action.copy() if target_action else bpy.data.actions.new("Merged_Action")
    duration = source_range[1] - source_range[0]
    
    for loop in range(loop_times):
        current_offset = time_offset + loop * duration
        
        for src_fcurve in source_action.fcurves:
            # 检查源曲线是否有关键帧
            if len(src_fcurve.keyframe_points) == 0:
                continue
                
            target_fcurve = next(
                (fc for fc in merged_action.fcurves 
                 if fc.data_path == src_fcurve.data_path 
                 and fc.array_index == src_fcurve.array_index),
                None
            )
            
            if not target_fcurve:
                target_fcurve = merged_action.fcurves.new(
                    src_fcurve.data_path,
                    index=src_fcurve.array_index
                )
            
            # 获取现有关键帧
            existing_keys = {}
            if target_fcurve.keyframe_points:  # 检查目标曲线是否有关键帧
                for k in target_fcurve.keyframe_points:
                    # 使用整数帧索引避免浮点精度问题
                    existing_keys[int(k.co.x)] = k
            
            # 预先计算要添加的关键帧
            keys_to_add = []
            for src_key in src_fcurve.keyframe_points:
                frame = src_key.co.x
                # 使用范围检查代替精确比较
                if not (source_range[0] <= frame <= source_range[1]):
                    continue
                    
                # 确保计算结果为整数帧，避免浮点精度问题
                target_frame = int(frame - source_range[0] + current_offset)
                
                if merge_mode == 'REPLACE':
                    # 先记录要添加的，后面一次性处理
                    keys_to_add.append((target_frame, src_key.co.y, src_key.interpolation))
                    
                elif merge_mode == 'MIX':
                    if target_frame in existing_keys:
                        existing_val = existing_keys[target_frame].co.y
                        new_val = (existing_val + src_key.co.y) * 0.5
                        keys_to_add.append((target_frame, new_val, src_key.interpolation))
                    else:
                        keys_to_add.append((target_frame, src_key.co.y, src_key.interpolation))
            
            # 如果是替换模式，先清除将被替换的关键帧
            if merge_mode == 'REPLACE' and keys_to_add:
                frames_to_remove = [frame for frame in existing_keys.keys() 
                                   if any(abs(frame - key[0]) < 0.5 for key in keys_to_add)]
                for frame in frames_to_remove:
                    if frame in existing_keys:
                        try:
                            target_fcurve.keyframe_points.remove(existing_keys[frame])
                        except:
                            # 如果删除失败，忽略错误继续处理
                            pass
            
            # 添加关键帧
            if keys_to_add:
                for frame, value, interp in keys_to_add:
                    try:
                        # 检查是否已存在该帧的关键帧（避免冲突）
                        existing = next((k for k in target_fcurve.keyframe_points if abs(k.co.x - frame) < 0.5), None)
                        if existing:
                            existing.co.y = value
                            existing.interpolation = interp
                        else:
                            new_key = target_fcurve.keyframe_points.insert(frame, value)
                            new_key.interpolation = interp
                    except Exception as e:
                        print(f"添加关键帧时出错: {str(e)}")
                        continue
            
            # 更新曲线
            try:
                target_fcurve.update()
            except:
                pass
    
    return merged_action

def merge_actions_with_transform(target_action, source_action, time_offset, source_range, merge_mode, target_armature, loop_times=1):

    # 确保参数为整数
    time_offset = int(time_offset)
    source_range = (int(source_range[0]), int(source_range[1]))
    
    # 记录原始变换
    original_location = target_armature.location.copy()
    original_rotation_euler = target_armature.rotation_euler.copy()
    original_rotation_mode = target_armature.rotation_mode
    original_rotation_quaternion = target_armature.rotation_quaternion.copy() if hasattr(target_armature, 'rotation_quaternion') else None
    
    # 检查源动作和目标动作
    if not source_action.fcurves:
        return target_action.copy() if target_action else bpy.data.actions.new("Merged_Action")
    
    # 先执行常规合并
    try:
        merged_action = merge_actions(target_action, source_action, time_offset, source_range, merge_mode, loop_times)
    except Exception as e:
        print(f"合并动作时出错: {str(e)}")
        # 如果合并失败，返回一个新的空动作或复制目标动作
        return target_action.copy() if target_action else bpy.data.actions.new("Merged_Action")
    
    # 获取帧范围
    duration = source_range[1] - source_range[0]
    total_duration = duration * loop_times
    
    # 对骨架对象级别的变换曲线进行处理
    location_curves = {}
    rotation_euler_curves = {}
    rotation_quaternion_curves = {}
    
    for fcurve in merged_action.fcurves:
        if fcurve.data_path == 'location':
            location_curves[fcurve.array_index] = fcurve
        elif fcurve.data_path == 'rotation_euler':
            rotation_euler_curves[fcurve.array_index] = fcurve
        elif fcurve.data_path == 'rotation_quaternion':
            rotation_quaternion_curves[fcurve.array_index] = fcurve
    
    # 处理位置曲线
    for axis, fcurve in location_curves.items():
        if not fcurve.keyframe_points:  # 检查是否有关键帧
            continue
        for kp in fcurve.keyframe_points:
            if time_offset <= kp.co.x <= time_offset + total_duration:
                kp.co.y += original_location[axis]
    
    # 处理欧拉旋转曲线
    for axis, fcurve in rotation_euler_curves.items():
        if not fcurve.keyframe_points:  # 检查是否有关键帧
            continue
        for kp in fcurve.keyframe_points:
            if time_offset <= kp.co.x <= time_offset + total_duration:
                kp.co.y += original_rotation_euler[axis]
    
    # 处理四元数旋转曲线（这里需要更复杂的四元数数学）
    if rotation_quaternion_curves and original_rotation_quaternion:
        orig_quat = original_rotation_quaternion
        
        # 收集所有关键帧
        frames = set()
        for axis, fcurve in rotation_quaternion_curves.items():
            if not fcurve.keyframe_points:  # 检查是否有关键帧
                continue
            for kp in fcurve.keyframe_points:
                if time_offset <= kp.co.x <= time_offset + total_duration:
                    # 使用整数帧来避免浮点精度问题
                    frames.add(int(kp.co.x))
        
        # 按帧处理四元数
        for frame in frames:
            # 获取当前帧的四元数
            current_quat = mathutils.Quaternion()
            for i in range(4):
                if i in rotation_quaternion_curves and rotation_quaternion_curves[i].keyframe_points:
                    for kp in rotation_quaternion_curves[i].keyframe_points:
                        if abs(kp.co.x - frame) < 0.5:  # 使用0.5作为阈值，更合理地匹配帧
                            current_quat[i] = kp.co.y
            
            # 应用原始四元数旋转（相乘）
            result_quat = orig_quat @ current_quat
            
            # 更新关键帧值
            for i in range(4):
                if i in rotation_quaternion_curves and rotation_quaternion_curves[i].keyframe_points:
                    for kp in rotation_quaternion_curves[i].keyframe_points:
                        if abs(kp.co.x - frame) < 0.5:  # 使用0.5作为阈值
                            kp.co.y = result_quat[i]
    
    # 更新所有曲线
    for fcurve in merged_action.fcurves:
        try:
            fcurve.update()
        except:
            pass
    
    return merged_action

def apply_action_sequence(target_action, source_action, target_start, source_range, merge_mode, loop_times, apply_transform=False, target_armature=None):

    # 确保参数为整数，避免浮点精度问题
    target_start = int(target_start)
    source_range = (int(source_range[0]), int(source_range[1]))
    
    result_action = target_action.copy() if target_action else bpy.data.actions.new("Merged_Action")
    
    # 第一次应用初始位置
    current_start_frame = target_start
    
    # 计算源动画的持续时间
    source_duration = source_range[1] - source_range[0]
    
    # 记录每次应用后的终点，作为下一次的起点
    current_end_frame = current_start_frame + source_duration
    
    # 记录原始位置增量（用于位移动画）
    original_location_start = None
    original_location_end = None
    location_delta = None
    
    # 查找并记录源动作中的第一帧，用于后续处理
    first_frame_in_source = int(source_range[0])
    
    # 找出源动作中的位置变化（对于走路等动画）
    if apply_transform and source_action:
        # 查找位置曲线
        location_curves = {}
        for fcurve in source_action.fcurves:
            if fcurve.data_path == 'location':
                location_curves[fcurve.array_index] = fcurve
        
        # 如果有位置曲线，计算起始和结束位置
        if location_curves:
            # 创建起始和结束位置向量
            original_location_start = mathutils.Vector([0, 0, 0])
            original_location_end = mathutils.Vector([0, 0, 0])
            
            # 用于精确匹配的误差阈值
            epsilon = 0.01
            
            # 填充位置数据
            for axis, fcurve in location_curves.items():
                if not fcurve.keyframe_points:
                    continue
                
                # 收集所有关键帧并按时间排序
                all_keys_in_range = [kp for kp in fcurve.keyframe_points if source_range[0] <= kp.co.x <= source_range[1]]
                all_keys_in_range.sort(key=lambda kp: kp.co.x)
                
                if all_keys_in_range:
                    # 使用范围内第一个和最后一个关键帧
                    original_location_start[axis] = all_keys_in_range[0].co.y
                    original_location_end[axis] = all_keys_in_range[-1].co.y
            
            # 计算位置变化量
            location_delta = original_location_end - original_location_start
            print(f"检测到位置变化: {location_delta}，起始：{original_location_start}，结束：{original_location_end}")
    
    # 记录每次循环的帧范围，用于后续更新
    applied_ranges = []
    
    # 依次应用每一个循环
    for i in range(loop_times):
        print(f"应用循环 {i+1}/{loop_times}，起始帧: {current_start_frame}")
        
        # 创建源动作的副本
        loop_source = source_action.copy()
        
        # 如果不是第一次循环，且有位置变化，则调整本次循环的所有关键帧位置
        if i > 0 and location_delta is not None:
            accumulated_delta = location_delta * i
            
            # 调整位置曲线的所有关键帧
            for fcurve in loop_source.fcurves:
                if fcurve.data_path == 'location':
                    axis = fcurve.array_index
                    # 确保变化值足够大才应用，避免浮点精度问题
                    if abs(accumulated_delta[axis]) > 0.0001:
                        for kp in fcurve.keyframe_points:
                            kp.co.y += accumulated_delta[axis]
        
        # 合并此次循环的动作
        if apply_transform and target_armature:
            temp_result = merge_actions_with_transform(
                result_action, 
                loop_source, 
                current_start_frame, 
                source_range, 
                merge_mode, 
                target_armature, 
                1  # 每次只合并一次
            )
        else:
            temp_result = merge_actions(
                result_action, 
                loop_source, 
                current_start_frame, 
                source_range, 
                merge_mode, 
                1  # 每次只合并一次
            )
        
        # 清理本次循环的临时动作
        bpy.data.actions.remove(loop_source)
        
        # 如果合并成功，更新结果动作
        if temp_result:
            # 先保存旧的结果动作
            old_result = result_action
            # 更新为新的结果
            result_action = temp_result
            # 删除旧的结果（如果不是第一个）
            if i > 0:
                bpy.data.actions.remove(old_result)
        
        # 记录本次应用的帧范围，确保使用整数帧
        applied_ranges.append((int(current_start_frame), int(current_end_frame)))
        
        # 更新下一次循环的起始帧（确保帧号为整数）
        current_start_frame = int(current_end_frame)
        current_end_frame = int(current_start_frame + source_duration)
    
    # 最后一步：删除所有循环衔接处的第一帧关键帧（除了第一个循环）
    if loop_times > 1:
        delete_first_frames_at_loop_boundaries(result_action, applied_ranges, first_frame_in_source)
    
    return result_action, applied_ranges

def delete_first_frames_at_loop_boundaries(action, applied_ranges, first_frame_in_source):

    if len(applied_ranges) <= 1:
        return  # 只有一个循环，不需要删除
    
    # 对于每个循环的起始点（第一个循环除外）
    for i in range(1, len(applied_ranges)):
        loop_start_frame = applied_ranges[i][0]
        
        # 计算这个循环中第一帧的绝对帧位置
        first_frame_absolute = loop_start_frame  # 循环起始帧就是第一帧的位置
        
        print(f"删除循环 {i} 开始处的第一帧关键帧，帧号: {first_frame_absolute}")
        
        # 查找并删除所有位于这个位置的关键帧
        for fcurve in action.fcurves:
            # 查找并收集这个位置的关键帧
            keyframes_to_remove = []
            for j, kp in enumerate(fcurve.keyframe_points):
                if abs(kp.co.x - first_frame_absolute) < 0.5:  # 使用容差匹配
                    keyframes_to_remove.append(j)
            
            # 从后向前删除（避免索引变化问题）
            for idx in reversed(keyframes_to_remove):
                try:
                    fcurve.keyframe_points.remove(fcurve.keyframe_points[idx])
                    print(f"从数据路径 {fcurve.data_path}[{fcurve.array_index}] 删除了帧 {first_frame_absolute} 的关键帧")
                except Exception as e:
                    print(f"删除关键帧时出错: {str(e)}")
            
            # 更新曲线
            try:
                fcurve.update()
            except:
                pass

class ANIM_OT_SaveCustomAction(bpy.types.Operator):
    bl_idname = "anim.save_custom_action"
    bl_label = "保存自定义动画"
    bl_options = {'REGISTER', 'UNDO'}
    
    name: bpy.props.StringProperty(name="动画名称", default="自定义动画")
    start_frame: bpy.props.IntProperty(name="起始帧", default=1, min=0)
    end_frame: bpy.props.IntProperty(name="结束帧", default=250, min=1)
    
    def execute(self, context):
        target = context.active_object
        if not target or target.type != 'ARMATURE' or not target.animation_data or not target.animation_data.action:
            self.report({'ERROR'}, "请选骨架")
            return {'CANCELLED'}
        
        original_action = target.animation_data.action
        new_action = original_action.copy()
        new_action.name = self.name

        for fcurve in new_action.fcurves:
            keyframes = []
            for kp in fcurve.keyframe_points:
                if self.start_frame <= kp.co.x <= self.end_frame:
                    keyframes.append((kp.co.x, kp.co.y, kp.interpolation))
            
            fcurve.keyframe_points.clear()
            for frame, value, interp in keyframes:
                kp = fcurve.keyframe_points.insert(frame, value)
                kp.interpolation = interp
            fcurve.update()

        safe_name = clean_filename(new_action.name)
        fbx_stem = "Custom_Actions"
        save_dir = LIBRARY_PATH / fbx_stem
        save_dir.mkdir(exist_ok=True)
        save_path = save_dir / f"{safe_name}.json"
        counter = 1
        while save_path.exists():
            save_path = save_dir / f"{safe_name}_{counter}.json"
            counter += 1
        
        serialize_action(new_action, save_path)
        
        item = context.scene.anim_merge_props.action_library_items.add()
        item.name = new_action.name
        item.filepath = str(save_path)
        item.source_fbx = fbx_stem
        item.frame_range = (self.start_frame, self.end_frame)
        
        bpy.data.actions.remove(new_action)
        self.report({'INFO'}, f"OK: {self.name}")
        return {'FINISHED'}
    
    def invoke(self, context, event):
        if context.active_object and context.active_object.animation_data and context.active_object.animation_data.action:
            action = context.active_object.animation_data.action
            self.name = action.name
            frames = []
            for fcurve in action.fcurves:
                frames.extend([kp.co.x for kp in fcurve.keyframe_points])
            if frames:
                self.start_frame = int(min(frames))
                self.end_frame = int(max(frames))
        return context.window_manager.invoke_props_dialog(self, width=300)
        
    def draw(self, context):
        layout = self.layout
        layout.label(text="保存动作到库", icon='FILE_TICK')
        layout.separator()
        
        layout.prop(self, "name", icon='ACTION')
        
        frame_row = layout.row(align=True)
        frame_row.label(text="帧范围:")
        frame_row.prop(self, "start_frame", text="从")
        frame_row.prop(self, "end_frame", text="到")
        
        layout.separator()
        layout.label(text="动作将存储在自定义动作库中", icon='INFO')

class ANIM_OT_ImportToLibrary(bpy.types.Operator):
    bl_idname = "anim.import_to_library"
    bl_label = "导入动作"
    bl_options = {'REGISTER', 'UNDO'}
    
    filter_glob: bpy.props.StringProperty(default="*.fbx", options={'HIDDEN'})
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    
    def execute(self, context):
        action = load_fbx_animation(self.filepath)
        if not action:
            self.report({'ERROR'}, "无效!!!")
            return {'CANCELLED'}
        safe_name = clean_filename(action.name)
        fbx_stem = clean_filename(Path(self.filepath).stem)

        save_dir = LIBRARY_PATH / fbx_stem
        save_dir.mkdir(exist_ok=True)
        save_path = save_dir / f"{safe_name}.json"
        counter = 1
        while save_path.exists():
            save_path = save_dir / f"{safe_name}_{counter}.json"
            counter += 1
        frames = []
        for fcurve in action.fcurves:
            frames.extend([kp.co.x for kp in fcurve.keyframe_points])
        frame_range = (int(min(frames)), int(max(frames))) if frames else (1, 250)
        serialize_action(action, save_path)
        item = context.scene.anim_merge_props.action_library_items.add()
        item.name = action.name
        item.filepath = str(save_path)
        item.source_fbx = fbx_stem
        item.frame_range = frame_range
        bpy.data.actions.remove(action)
        self.report({'INFO'}, f"已保存: {action.name}")
        return {'FINISHED'}
    
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class ANIM_OT_UseLibraryAction(bpy.types.Operator):
    bl_idname = "anim.use_library_action"
    bl_label = "应用动作"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        props = context.scene.anim_merge_props
        if not props.action_library_items:
            self.report({'ERROR'}, "你没资产")
            return {'CANCELLED'}
        item = props.action_library_items[props.action_library_index]
        target = context.active_object
        if not target or target.type != 'ARMATURE':
            self.report({'ERROR'}, "选骨架!!!")
            return {'CANCELLED'}
        props.source_start = item.frame_range[0]
        props.source_end = item.frame_range[1]
        
        # 设置标记，表示来自库
        props.is_from_library = True
        
        # 填充骨骼选择列表
        context.scene.bone_selection.clear()
        for bone in target.data.bones:
            item = context.scene.bone_selection.add()
            item.name = bone.name
            item.selected = True
        
        # 打开骨骼选择对话框
        bpy.ops.anim.select_bones('INVOKE_DEFAULT')
        return {'FINISHED'}

class ANIM_OT_UseLibraryActionWithBones(bpy.types.Operator):
    bl_idname = "anim.use_library_action_with_bones"
    bl_label = "带骨骼过滤的应用动作"
    bl_options = {'REGISTER', 'UNDO'}
    
    selected_bones: bpy.props.StringProperty(default="")
    bone_mapping: bpy.props.StringProperty(default="")
    
    def execute(self, context):
        props = context.scene.anim_merge_props
        if not props.action_library_items:
            self.report({'ERROR'}, "你没资产")
            return {'CANCELLED'}
        item = props.action_library_items[props.action_library_index]
        action = deserialize_action(Path(item.filepath))
        if not action:
            self.report({'ERROR'}, "动作加载失败")
            return {'CANCELLED'}
        target = context.active_object
        if not target or target.type != 'ARMATURE':
            self.report({'ERROR'}, "选骨架!!!")
            return {'CANCELLED'}
        
        # 解析选中的骨骼
        selected_bones = self.selected_bones.split(',') if self.selected_bones else []
        if not selected_bones:
            for bone_item in context.scene.bone_selection:
                if bone_item.selected:
                    selected_bones.append(bone_item.name)
        
        # 解析骨骼映射
        bone_mapping = {}
        if self.bone_mapping:
            try:
                bone_mapping = json.loads(self.bone_mapping)
                print(f"成功解析骨骼映射: {bone_mapping}")
            except Exception as e:
                self.report({'WARNING'}, f"骨骼映射解析失败: {str(e)}")
                print(f"骨骼映射解析失败: {str(e)}")
                print(f"原始映射数据: {self.bone_mapping}")
                bone_mapping = {}
        
        # 打印调试信息
        print("\n===== 动作应用前的调试信息 =====")
        print(f"选中的骨骼: {selected_bones}")
        print(f"骨骼映射: {bone_mapping}")
        print(f"启用骨骼映射: {props.enable_bone_mapping}")
        
        # 分析源动作
        print("\n源动作中的骨骼路径:")
        source_bones = set()
        for fcurve in action.fcurves:
            if "pose.bones" in fcurve.data_path:
                match = re.search(r'pose\.bones\["([^"]+)"\]', fcurve.data_path)
                if match:
                    bone_name = match.group(1)
                    source_bones.add(bone_name)
                    
        print(f"源动作中的骨骼: {sorted(list(source_bones))}")
        print("===== 动作应用前的调试信息结束 =====\n")
        
        # 根据选中的骨骼筛选fcurves
        if selected_bones:
            # 创建一个新动作，只包含选中骨骼的数据
            filtered_action = bpy.data.actions.new(name=f"{action.name}_filtered")
            
            # 调试计数器
            applied_curves = 0
            
            for fcurve in action.fcurves:
                # 检查数据路径是否属于选中的骨骼或是物体级别的动画
                is_selected = False
                mapped_path = fcurve.data_path
                
                # 处理骨骼级别的动画曲线
                if "pose.bones" in fcurve.data_path:
                    # 提取骨骼名称
                    match = re.search(r'pose\.bones\["([^"]+)"\]', fcurve.data_path)
                    if match:
                        bone_name = match.group(1)  # 源文件中的骨骼名
                        
                        # 详细调试信息
                        print(f"\n处理曲线 - 骨骼名: {bone_name}, 路径: {fcurve.data_path}")
                        
                        # 判断是否启用骨骼映射
                        if props.enable_bone_mapping and bone_mapping:
                            # 1. 检查源骨骼是否有映射
                            if bone_name in bone_mapping:
                                # 这里是关键修改: 先确定目标骨骼名称
                                target_bone = bone_mapping[bone_name]  # 映射后的目标骨骼名
                                print(f"  应用映射: {bone_name} -> {target_bone}")
                                
                                # 检查目标骨骼是否存在于选中骨骼中
                                is_selected = True  # 如果有映射，总是应用
                                                                
                                # 修改数据路径
                                mapped_path = fcurve.data_path.replace(
                                    f'pose.bones["{bone_name}"]',
                                    f'pose.bones["{target_bone}"]'
                                )
                                print(f"  修改路径: {fcurve.data_path} -> {mapped_path}")
                            else:
                                # 如果没有映射, 则源骨骼名和目标骨骼名相同
                                print(f"  无映射, 检查是否选中: {bone_name in selected_bones}")
                                is_selected = bone_name in selected_bones
                        else:
                            # 不使用映射时，使用原始骨骼名
                            print(f"  不使用映射, 检查是否选中: {bone_name in selected_bones}")
                            is_selected = bone_name in selected_bones
                else:
                    # 物体级别的动画曲线始终保留
                    print(f"  物体级动画曲线: {fcurve.data_path}")
                    is_selected = True
                
                # 如果是选中的骨骼或物体级动画，添加到过滤后的动作
                if is_selected:
                    try:
                        new_curve = filtered_action.fcurves.new(
                            mapped_path, 
                            index=fcurve.array_index
                        )
                        for kp in fcurve.keyframe_points:
                            new_kp = new_curve.keyframe_points.insert(kp.co.x, kp.co.y)
                            new_kp.interpolation = kp.interpolation
                        
                        applied_curves += 1
                        print(f"  成功添加曲线: {mapped_path}[{fcurve.array_index}]")
                    except Exception as e:
                        print(f"  添加曲线失败: {mapped_path}[{fcurve.array_index}], 错误: {str(e)}")
            
            # 使用过滤后的动作进行连续应用
            print(f"\n应用了 {applied_curves} 条曲线")
            
            if applied_curves == 0:
                print("警告: 没有应用任何动画曲线!")
                self.report({'WARNING'}, "没有应用任何动画曲线，请检查骨骼选择和映射")
            
            merged_action, applied_ranges = apply_action_sequence(
                target.animation_data.action if target.animation_data else None,
                filtered_action,
                props.target_start,
                (props.source_start, props.source_end),
                props.merge_mode,
                props.loop_times,
                props.apply_transform,
                target
            )
                
            # 清理临时动作
            bpy.data.actions.remove(filtered_action)
        else:
            # 如果没有选中骨骼，按原来的方式处理
            merged_action, applied_ranges = apply_action_sequence(
                target.animation_data.action if target.animation_data else None,
                action,
                props.target_start,
                (props.source_start, props.source_end),
                props.merge_mode,
                props.loop_times,
                props.apply_transform,
                target
            )
                
        # 应用合并后的动作
        if not target.animation_data:
            target.animation_data_create()
        target.animation_data.action = merged_action
        
        # 更新场景帧范围，确保能看到所有动画
        if applied_ranges:
            context.scene.frame_end = max(
                context.scene.frame_end,
                applied_ranges[-1][1]  # 最后一个范围的结束帧
            )
        
        # 清理原始动作
        bpy.data.actions.remove(action)
        
        self.report({'INFO'}, f"OK: {item.name} 已连续应用 {props.loop_times} 次")
        return {'FINISHED'}

class ANIM_OT_MergeFBX(bpy.types.Operator):
    bl_idname = "anim.merge_fbx"
    bl_label = "合并FBX动画至你的骨骼"
    bl_options = {'REGISTER', 'UNDO'}

    filter_glob: bpy.props.StringProperty(default="*.fbx", options={'HIDDEN'})
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    
    def execute(self, context):
        props = context.scene.anim_merge_props
        target = context.active_object
        
        if not target or target.type != 'ARMATURE':
            self.report({'ERROR'}, "请选择目标骨架")
            return {'CANCELLED'}
        
        # 保存文件路径
        props.filepath = self.filepath
        # 设置标记，表示不是来自库
        props.is_from_library = False
        
        # 填充骨骼选择列表
        context.scene.bone_selection.clear()
        for bone in target.data.bones:
            item = context.scene.bone_selection.add()
            item.name = bone.name
            item.selected = True
        
        # 打开骨骼选择对话框
        bpy.ops.anim.select_bones('INVOKE_DEFAULT')
        return {'FINISHED'}
    
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class ANIM_OT_MergeFBXWithBones(bpy.types.Operator):
    bl_idname = "anim.merge_fbx_with_bones"
    bl_label = "带骨骼过滤的合并FBX"
    bl_options = {'REGISTER', 'UNDO'}
    
    selected_bones: bpy.props.StringProperty(default="")
    bone_mapping: bpy.props.StringProperty(default="")
    
    def execute(self, context):
        props = context.scene.anim_merge_props
        target = context.active_object
        
        if not target or target.type != 'ARMATURE':
            self.report({'ERROR'}, "请选择目标骨架")
            return {'CANCELLED'}
        
        action = load_fbx_animation(props.filepath)
        if not action:
            self.report({'ERROR'}, "读取失败")
            return {'CANCELLED'}
            
        # 解析选中的骨骼
        selected_bones = self.selected_bones.split(',') if self.selected_bones else []
        if not selected_bones:
            for bone_item in context.scene.bone_selection:
                if bone_item.selected:
                    selected_bones.append(bone_item.name)
        
        # 解析骨骼映射
        bone_mapping = {}
        if self.bone_mapping:
            try:
                bone_mapping = json.loads(self.bone_mapping)
                print(f"成功解析骨骼映射: {bone_mapping}")
            except Exception as e:
                self.report({'WARNING'}, f"骨骼映射解析失败: {str(e)}")
                print(f"骨骼映射解析失败: {str(e)}")
                print(f"原始映射数据: {self.bone_mapping}")
                bone_mapping = {}
        
        # 打印调试信息
        print("\n===== 动作应用前的调试信息 =====")
        print(f"选中的骨骼: {selected_bones}")
        print(f"骨骼映射: {bone_mapping}")
        print(f"启用骨骼映射: {props.enable_bone_mapping}")
        
        # 分析源动作
        print("\n源动作中的骨骼路径:")
        source_bones = set()
        for fcurve in action.fcurves:
            if "pose.bones" in fcurve.data_path:
                match = re.search(r'pose\.bones\["([^"]+)"\]', fcurve.data_path)
                if match:
                    bone_name = match.group(1)
                    source_bones.add(bone_name)
                    
        print(f"源动作中的骨骼: {sorted(list(source_bones))}")
        print("===== 动作应用前的调试信息结束 =====\n")
        
        # 根据选中的骨骼筛选fcurves
        if selected_bones:
            # 创建一个新动作，只包含选中骨骼的数据
            filtered_action = bpy.data.actions.new(name=f"{action.name}_filtered")
            
            # 调试计数器
            applied_curves = 0
            
            for fcurve in action.fcurves:
                # 检查数据路径是否属于选中的骨骼或是物体级别的动画
                is_selected = False
                mapped_path = fcurve.data_path
                
                # 处理骨骼级别的动画曲线
                if "pose.bones" in fcurve.data_path:
                    # 提取骨骼名称
                    match = re.search(r'pose\.bones\["([^"]+)"\]', fcurve.data_path)
                    if match:
                        bone_name = match.group(1)  # 源文件中的骨骼名
                        
                        # 详细调试信息
                        print(f"\n处理曲线 - 骨骼名: {bone_name}, 路径: {fcurve.data_path}")
                        
                        # 判断是否启用骨骼映射
                        if props.enable_bone_mapping and bone_mapping:
                            # 1. 检查源骨骼是否有映射
                            if bone_name in bone_mapping:
                                # 这里是关键修改: 先确定目标骨骼名称
                                target_bone = bone_mapping[bone_name]  # 映射后的目标骨骼名
                                print(f"  应用映射: {bone_name} -> {target_bone}")
                                
                                # 检查目标骨骼是否存在于选中骨骼中
                                is_selected = True  # 如果有映射，总是应用
                                                                
                                # 修改数据路径
                                mapped_path = fcurve.data_path.replace(
                                    f'pose.bones["{bone_name}"]',
                                    f'pose.bones["{target_bone}"]'
                                )
                                print(f"  修改路径: {fcurve.data_path} -> {mapped_path}")
                            else:
                                # 如果没有映射, 则源骨骼名和目标骨骼名相同
                                print(f"  无映射, 检查是否选中: {bone_name in selected_bones}")
                                is_selected = bone_name in selected_bones
                        else:
                            # 不使用映射时，使用原始骨骼名
                            print(f"  不使用映射, 检查是否选中: {bone_name in selected_bones}")
                            is_selected = bone_name in selected_bones
                else:
                    # 物体级别的动画曲线始终保留
                    print(f"  物体级动画曲线: {fcurve.data_path}")
                    is_selected = True
                
                # 如果是选中的骨骼或物体级动画，添加到过滤后的动作
                if is_selected:
                    try:
                        new_curve = filtered_action.fcurves.new(
                            mapped_path, 
                            index=fcurve.array_index
                        )
                        for kp in fcurve.keyframe_points:
                            new_kp = new_curve.keyframe_points.insert(kp.co.x, kp.co.y)
                            new_kp.interpolation = kp.interpolation
                        
                        applied_curves += 1
                        print(f"  成功添加曲线: {mapped_path}[{fcurve.array_index}]")
                    except Exception as e:
                        print(f"  添加曲线失败: {mapped_path}[{fcurve.array_index}], 错误: {str(e)}")
            
            # 使用过滤后的动作进行连续应用
            print(f"\n应用了 {applied_curves} 条曲线")
            
            if applied_curves == 0:
                print("警告: 没有应用任何动画曲线!")
                self.report({'WARNING'}, "没有应用任何动画曲线，请检查骨骼选择和映射")
            
            merged_action, applied_ranges = apply_action_sequence(
                target.animation_data.action if target.animation_data else None,
                filtered_action,
                props.target_start,
                (props.source_start, props.source_end),
                props.merge_mode,
                props.loop_times,
                props.apply_transform,
                target
            )
                
            # 清理临时动作
            bpy.data.actions.remove(filtered_action)
        else:
            # 如果没有选中骨骼，按原来的方式处理
            merged_action, applied_ranges = apply_action_sequence(
                target.animation_data.action if target.animation_data else None,
                action,
                props.target_start,
                (props.source_start, props.source_end),
                props.merge_mode,
                props.loop_times,
                props.apply_transform,
                target
            )
                
        # 应用合并后的动作
        if not target.animation_data:
            target.animation_data_create()
        target.animation_data.action = merged_action
        
        # 更新场景帧范围，确保能看到所有动画
        if applied_ranges:
            context.scene.frame_end = max(
                context.scene.frame_end,
                applied_ranges[-1][1]  # 最后一个范围的结束帧
            )
        
        # 清理原始动作
        bpy.data.actions.remove(action)
        
        self.report({'INFO'}, f"合并完成! 已连续应用 {props.loop_times} 次")
        return {'FINISHED'}

class ANIM_OT_SelectBones(bpy.types.Operator):
    bl_idname = "anim.select_bones"
    bl_label = "选择要复制的骨骼"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        # 操作完成后继续执行动作合并
        if hasattr(context.scene, "bone_selection"):
            bone_selection = context.scene.bone_selection
            props = context.scene.anim_merge_props
            
            # 打印骨骼选择情况
            print("\n===== 骨骼选择和映射信息 =====")
            
            # 筛选选中的骨骼
            selected_bones = [item.name for item in bone_selection if item.selected]
            print(f"选中的骨骼数量: {len(selected_bones)}")
            
            # 判断是否有选中的骨骼
            if not selected_bones:
                self.report({'INFO'}, "未选择任何骨骼，操作已取消")
                return {'CANCELLED'}
            
            # 获取映射信息(如果启用)
            bone_mapping = {}
            if context.scene.anim_merge_props.enable_bone_mapping:
                print(f"启用了骨骼映射")
                for item in bone_selection:
                    print(f"骨骼项: {item.name}, 选中: {item.selected}, 启用映射: {item.use_mapping}, 映射到: {item.mapped_to}")
                    if item.selected and item.use_mapping and item.mapped_to:
                        # 在启用骨骼映射时，将源骨骼映射到目标骨骼
                        # item.name现在是源文件中的骨骼名称
                        bone_mapping[item.name] = item.mapped_to
                        print(f"添加映射: {item.name} -> {item.mapped_to}")
            
            # 将骨骼列表转换为逗号分隔的字符串
            selected_bones_str = ",".join(selected_bones)
            
            # 将映射信息转换为JSON字符串
            mapping_json = json.dumps(bone_mapping) if bone_mapping else ""
            print(f"最终映射数据: {mapping_json}")
            print(f"启用骨骼映射: {props.enable_bone_mapping}")
            print("===== 骨骼选择和映射信息结束 =====\n")
            
            # 传递选中的骨骼和映射信息到合并函数
            if context.scene.anim_merge_props.is_from_library:
                bpy.ops.anim.use_library_action_with_bones('EXEC_DEFAULT', 
                                                         selected_bones=selected_bones_str,
                                                         bone_mapping=mapping_json)
            else:
                bpy.ops.anim.merge_fbx_with_bones('EXEC_DEFAULT', 
                                                selected_bones=selected_bones_str,
                                                bone_mapping=mapping_json)
                
        return {'FINISHED'}
    
    def invoke(self, context, event):
        props = context.scene.anim_merge_props
        
        # 清空骨骼选择列表
        context.scene.bone_selection.clear()
        
        # 如果启用骨骼映射，获取源文件中的骨骼列表
        if props.enable_bone_mapping:
            # 使用共用函数加载源骨骼
            source_bones = load_source_bones(context, props)
            
            # 如果成功获取到源骨骼，添加到选择列表
            if source_bones:
                for bone_name in sorted(source_bones):
                    item = context.scene.bone_selection.add()
                    item.name = bone_name
                    item.selected = True
                    # 默认不启用映射
                    item.use_mapping = False
                    item.mapped_to = ""
            else:
                # 如果没有获取到源骨骼，使用当前骨架的骨骼
                self.report({'WARNING'}, "未找到源文件中的骨骼，将使用当前骨架的骨骼")
                active_obj = context.active_object
                if active_obj and active_obj.type == 'ARMATURE':
                    for bone in active_obj.data.bones:
                        item = context.scene.bone_selection.add()
                        item.name = bone.name
                        item.selected = True
                        item.use_mapping = False
                        item.mapped_to = ""
        else:
            # 不启用骨骼映射时，使用当前骨架的骨骼
            active_obj = context.active_object
            if active_obj and active_obj.type == 'ARMATURE':
                for bone in active_obj.data.bones:
                    item = context.scene.bone_selection.add()
                    item.name = bone.name
                    item.selected = True
                    item.use_mapping = False
                    item.mapped_to = ""
        
        return context.window_manager.invoke_props_dialog(self, width=450)
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.anim_merge_props
        
        # 判断是否有骨骼可选
        has_bones = len(context.scene.bone_selection) > 0
        
        if props.enable_bone_mapping:
            title = "选择要映射的源骨骼:"
        else:
            title = "选择要复制动画的骨骼:"
            
        layout.label(text=title, icon='BONE_DATA')
        
        # 添加骨骼映射开关
        mapping_box = layout.box()
        
        # 使用更紧凑的布局
        top_row = mapping_box.row(align=True)
        top_row.prop(props, "enable_bone_mapping", text="启用骨骼映射")
        help_btn = top_row.row()
        help_btn.alignment = 'RIGHT'
        help_btn.prop(props, "show_mapping_help", text="", icon='QUESTION')
        
        # 添加自动映射和刷新按钮
        buttons_row = mapping_box.row(align=True)
        buttons_row.scale_y = 1.2
        buttons_row.operator("anim.auto_bone_mapping", text="自动映射", icon='AUTO')
        buttons_row.operator("anim.reload_bone_mapping", text="重新加载", icon='FILE_REFRESH')
        
        # 显示帮助信息
        if props.show_mapping_help:
            help_box = mapping_box.box()
            help_box.label(text="骨骼映射使用说明:", icon='INFO')
            help_box.label(text="1. 勾选要使用的源骨骼")
            help_box.label(text="2. 对需要映射的骨骼启用映射")
            help_box.label(text="3. 点击🔍按钮搜索目标骨骼")
            help_box.label(text="例如: leg_left(源) → Leg.L(目标)")
        
        if has_bones:
            # 添加全选/全不选按钮
            button_row = layout.row(align=True)
            button_row.scale_y = 1.2
            button_row.operator("anim.select_all_bones", text="全选", icon='CHECKBOX_HLT').select_all = True
            button_row.operator("anim.select_all_bones", text="全不选", icon='CHECKBOX_DEHLT').select_all = False
            
            # 显示骨骼列表
            box = layout.box()
            
            # 计算合适的列数
            total_bones = len(context.scene.bone_selection)
            
            if props.enable_bone_mapping:
                # 单列布局以适应更多控件
                col = box.column(align=True)
                
                if props.is_from_library:
                    source_type = "JSON文件"
                else:
                    source_type = "FBX文件"
                
                # 添加标题行，清楚标明左侧是源骨骼，右侧是目标骨骼
                header_row = col.row()
                header_row.label(text=f"源骨骼({source_type})")
                header_row.label(text="启用映射")
                header_row.label(text="目标骨骼(当前骨架)")
                
                for i, item in enumerate(context.scene.bone_selection):
                    row = col.row(align=True)
                    # 选中复选框
                    row.prop(item, "selected", text="")
                    # 骨骼名称
                    row.label(text=item.name)
                    # 启用映射复选框
                    map_toggle = row.row()
                    map_toggle.enabled = item.selected
                    map_toggle.prop(item, "use_mapping", text="", icon='ARROW_LEFTRIGHT')
                    # 映射目标输入框和搜索按钮
                    if item.selected and item.use_mapping:
                        map_field = row.row(align=True)
                        map_field.prop(item, "mapped_to", text="")
                        # 添加搜索按钮
                        search_op = map_field.operator("anim.search_bone", text="", icon='VIEWZOOM')
                        search_op.bone_index = i
                    else:
                        # 如果没有启用映射，添加一个空的占位符
                        row.label(text="")
            else:
                # 标准网格布局
                if total_bones > 20:
                    grid = box.grid_flow(row_major=True, columns=2, even_columns=True)
                    for item in context.scene.bone_selection:
                        row = grid.row(align=True)
                        row.prop(item, "selected", text="")
                        row.label(text=item.name)
                else:
                    # 单列时使用普通列布局
                    col = box.column(align=True)
                    for item in context.scene.bone_selection:
                        row = col.row(align=True)
                        row.prop(item, "selected", text="")
                        row.label(text=item.name)
        else:
            layout.label(text="未找到骨骼，请先选择一个骨架", icon='ERROR')
                
        # 添加底部提示
        layout.label(text="提示: 未选择任何骨骼将取消操作", icon='INFO')

class ANIM_OT_SelectAllBones(bpy.types.Operator):
    bl_idname = "anim.select_all_bones"
    bl_label = "全选/全不选骨骼"
    bl_options = {'REGISTER', 'UNDO'}
    
    select_all: bpy.props.BoolProperty()
    
    def execute(self, context):
        for item in context.scene.bone_selection:
            item.selected = self.select_all
        return {'FINISHED'}

class ANIM_UL_ActionLibrary(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            split = layout.split(factor=0.6)
            split.label(text=item.name, icon='ACTION')
            split.label(text=f"{item.source_fbx} [{item.frame_range[0]}-{item.frame_range[1]}]")
        elif self.layout_type in {'GRID'}:
            layout.alignment = 'CENTER'
            layout.label(text=item.name, icon='ACTION')

class ANIM_OT_ExportAction(bpy.types.Operator):
    bl_idname = "anim.export_action"
    bl_label = "导出动作为JSON"
    bl_options = {'REGISTER', 'UNDO'}
    
    filepath: bpy.props.StringProperty(
        subtype="FILE_PATH",
        default="animation.json"
    )
    filter_glob: bpy.props.StringProperty(
        default="*.json",
        options={'HIDDEN'}
    )
    
    def execute(self, context):
        target = context.active_object
        if not target or target.type != 'ARMATURE' or not target.animation_data or not target.animation_data.action:
            self.report({'ERROR'}, "未选择含有动作的骨架")
            return {'CANCELLED'}
        
        action = target.animation_data.action
        
        # 使用现有的序列化函数
        try:
            serialize_action(action, self.filepath)
            self.report({'INFO'}, f"成功导出动作: {action.name}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"导出失败: {str(e)}")
            return {'CANCELLED'}
    
    def invoke(self, context, event):
        if context.active_object and context.active_object.animation_data and context.active_object.animation_data.action:
            self.filepath = clean_filename(context.active_object.animation_data.action.name) + ".json"
        
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class ANIM_OT_ImportActionFromJSON(bpy.types.Operator):
    bl_idname = "anim.import_json"
    bl_label = "导入JSON到动作库"
    bl_options = {'REGISTER', 'UNDO'}
    
    filepath: bpy.props.StringProperty(
        subtype="FILE_PATH"
    )
    filter_glob: bpy.props.StringProperty(
        default="*.json",
        options={'HIDDEN'}
    )
    
    def execute(self, context):
        try:
            # 使用现有的反序列化函数临时加载动作
            action = deserialize_action(self.filepath)
            if not action:
                self.report({'ERROR'}, "JSON文件格式错误或不包含有效动作")
                return {'CANCELLED'}
            
            # 获取动作的帧范围
            frames = []
            for fcurve in action.fcurves:
                frames.extend([kp.co.x for kp in fcurve.keyframe_points])
            frame_range = (int(min(frames)), int(max(frames))) if frames else (1, 250)
            
            # 准备保存路径
            json_path = Path(self.filepath)
            safe_name = clean_filename(action.name)
            fbx_stem = "Imported_JSON"
            save_dir = LIBRARY_PATH / fbx_stem
            save_dir.mkdir(exist_ok=True)
            save_path = save_dir / f"{safe_name}.json"
            counter = 1
            while save_path.exists():
                save_path = save_dir / f"{safe_name}_{counter}.json"
                counter += 1
            
            # 复制JSON文件到库目录
            with open(json_path, 'r', encoding='utf-8') as src_file:
                action_data = json.load(src_file)
                with open(save_path, 'w', encoding='utf-8') as dest_file:
                    json.dump(action_data, dest_file, indent=2, ensure_ascii=False)
            
            # 添加到动作库
            item = context.scene.anim_merge_props.action_library_items.add()
            item.name = action.name
            item.filepath = str(save_path)
            item.source_fbx = fbx_stem
            item.frame_range = frame_range
            
            # 清理临时动作
            bpy.data.actions.remove(action)
            
            self.report({'INFO'}, f"成功导入动作: {item.name}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"导入失败: {str(e)}")
            return {'CANCELLED'}
    
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class ANIM_PT_MainPanel(bpy.types.Panel):
    bl_label = "骨骼动画管理工具"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "动画工具"
    
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        props = scene.anim_merge_props
        
        # 功能选择按钮
        box = layout.box()
        
        # 添加标题
        row = box.row(align=True)
        row.alignment = 'CENTER'
        row.label(text="功能选择", icon="TOOL_SETTINGS")
        
        # --- 第一行 ---
        row = box.row(align=True)
        row.scale_y = 1.2
        split = row.split(factor=0.5, align=True)
        col1 = split.column(align=True)
        col2 = split.column(align=True)

        # 动画合并按钮
        props_enum = col1.operator("wm.context_set_enum", text="动画合并", icon="ANIM", depress=(props.active_panel == 'MERGE'))
        props_enum.data_path = "scene.anim_merge_props.active_panel"
        props_enum.value = 'MERGE'

        # 动作库按钮
        props_enum = col2.operator("wm.context_set_enum", text="动作库", icon="ACTION", depress=(props.active_panel == 'LIBRARY'))
        props_enum.data_path = "scene.anim_merge_props.active_panel"
        props_enum.value = 'LIBRARY'

        # --- 第二行 ---
        row = box.row(align=True)
        row.scale_y = 1.2
        split = row.split(factor=0.5, align=True)
        col1 = split.column(align=True)
        col2 = split.column(align=True)

        # 路径动画按钮
        props_enum = col1.operator("wm.context_set_enum", text="路径动画", icon="CURVE_PATH", depress=(props.active_panel == 'PATH'))
        props_enum.data_path = "scene.anim_merge_props.active_panel"
        props_enum.value = 'PATH'

        # 呼吸动画按钮
        props_enum = col2.operator("wm.context_set_enum", text="呼吸动画", icon="ARMATURE_DATA", depress=(props.active_panel == 'BREATH'))
        props_enum.data_path = "scene.anim_merge_props.active_panel"
        props_enum.value = 'BREATH'

        # --- 第三行 ---
        row = box.row(align=True)
        row.scale_y = 1.2
        split = row.split(factor=0.5, align=True)
        col1 = split.column(align=True)
        col2 = split.column(align=True)

        # 摄像机动画按钮 (新添加)
        props_enum = col1.operator("wm.context_set_enum", text="摄像机动画", icon="CAMERA_DATA", depress=(props.active_panel == 'CAMERA'))
        props_enum.data_path = "scene.anim_merge_props.active_panel"
        props_enum.value = 'CAMERA'

        # 更新与信息按钮 (移到右侧)
        props_enum = col2.operator("wm.context_set_enum", text="更新与信息", icon="INFO", depress=(props.active_panel == 'UPDATE'))
        props_enum.data_path = "scene.anim_merge_props.active_panel"
        props_enum.value = 'UPDATE'
        
        # 添加在线动作库按钮 (第四行)
        row = box.row(align=True)
        row.scale_y = 1.2
        props_enum = row.operator("wm.context_set_enum", text="在线动作库", icon="WORLD", depress=(props.active_panel == 'ONLINE'))
        props_enum.data_path = "scene.anim_merge_props.active_panel"
        props_enum.value = 'ONLINE'

class ANIM_PT_ActionLibrary(bpy.types.Panel):
    bl_label = "动作资产库"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "动画工具"
    
    @classmethod
    def poll(cls, context):
        return context.scene.anim_merge_props.active_panel == 'LIBRARY'
    
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        props = scene.anim_merge_props
        
        # 标题栏改为使用分栏布局
        box = layout.box()
        header_row = box.row(align=True)
        header_row.label(text="动作库", icon='LIBRARY_DATA_DIRECT')
        
        # 操作按钮 - 添加新导入/导出按钮
        button_box = box.box()
        
        # 第一行：导入按钮
        import_row = button_box.row(align=True)
        import_row.operator("anim.import_to_library", text="导入FBX", icon='IMPORT')
        import_row.operator("anim.import_json", text="导入JSON", icon='FILE_TEXT')
        
        # 第二行：导出/保存按钮
        export_row = button_box.row(align=True)
        export_row.operator("anim.export_action", text="导出JSON", icon='EXPORT')
        export_row.operator("anim.save_custom_action", text="保存到库", icon='FILE_TICK')
        
        # 折叠展开按钮 
        box.prop(props, "show_library", 
            icon='DOWNARROW_HLT' if props.show_library else 'RIGHTARROW',
            text="显示库内容" if not props.show_library else "隐藏库内容",
            emboss=True
        )
        
        if props.show_library:
            list_box = box.box()
            list_box.template_list(
                "ANIM_UL_ActionLibrary", "",
                props, "action_library_items",
                props, "action_library_index",
                rows=5
            )
            use_row = box.row(align=True)
            use_row.scale_y = 1.2
            use_row.operator("anim.use_library_action", text="应用选中动作", icon='PLAY')
            # Add the delete button
            delete_row = box.row(align=True)
            delete_row.scale_y = 1.2
            # Pass confirm=False if not using invoke_confirm, or remove it
            delete_row.operator(ANIM_OT_DeleteLibraryAction.bl_idname, text="删除选中动作", icon='TRASH')

            # Add the move button
            # [USER REQUEST] Removing move button UI
            # move_row = box.row(align=True)
            # move_row.scale_y = 1.2
            # # Enable move only if other folders exist and an action is selected
            # can_move = False
            # if props.action_library_items and props.action_library_index >= 0:
            #     available_folders = get_library_folders(None, context)
            #     # Check if there's at least one folder that is not '_NO_FOLDERS_' and more than one total entry (including current)
            #     if any(f[0] != "_NO_FOLDERS_" for f in available_folders) and len(available_folders) > 1:
            #         can_move = True
            # # Set the enabled state of the row *before* adding the operator
            # move_row.enabled = can_move
            # move_row.operator(ANIM_OT_MoveLibraryAction.bl_idname, text="移动选中动作", icon='FILE_FOLDER')

class ANIM_PT_MergeControl(bpy.types.Panel):
    bl_label = "FBX动画合并"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "动画工具"
    
    @classmethod
    def poll(cls, context):
        return context.scene.anim_merge_props.active_panel == 'MERGE'
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.anim_merge_props
        
        # 时间轴控制区域
        time_box = layout.box()
        col = time_box.column(align=True)
        col.label(text="时间控制", icon='TIME')
        
        # 源帧范围设置
        source_row = col.row(align=True)
        source_row.label(text="源帧范围:")
        source_row.prop(props, "source_start", text="")
        source_row.prop(props, "source_end", text="")
        
        # 目标起始帧设置
        target_row = col.row(align=True)
        target_row.label(text="目标起始帧:")
        target_row.prop(props, "target_start", text="")
        
        # 循环设置
        loop_row = col.row(align=True)
        loop_row.label(text="循环次数:")
        loop_row.prop(props, "loop_times", text="")
        
        # 合并策略
        merge_box = layout.box()
        merge_box.label(text="合并策略", icon='MODIFIER')
        mode_row = merge_box.row()
        mode_row.prop(props, "merge_mode", expand=True)
        merge_box.prop(props, "apply_transform", icon='ORIENTATION_LOCAL')
        
        # 当前状态信息
        info_box = layout.box()
        info_box.label(text="当前状态", icon='INFO')
        obj = context.active_object
        if obj and obj.type == 'ARMATURE':
            state_row = info_box.row()
            state_row.label(text=f"目标骨架: {obj.name}", icon='ARMATURE_DATA')
            
            if obj.animation_data and obj.animation_data.action:
                action = obj.animation_data.action
                frame_count = sum(len(fc.keyframe_points) for fc in action.fcurves)
                action_row = info_box.row()
                action_row.label(text=f"当前动作: {action.name}", icon='ACTION')
                info_box.label(text=f"关键帧总数: {frame_count}")
                info_box.label(text=f"帧范围: {int(action.frame_range[0])}-{int(action.frame_range[1])}")
            else:
                info_box.label(text="没有检测到动画", icon='KEYFRAME_HLT')
                
            if obj.location.length > 0.001 or obj.rotation_euler.to_quaternion().angle > 0.001:
                warning_box = info_box.box()
                warning_box.alert = True
                warning_box.label(text=f"注意: 骨架不在原点/有旋转", icon='ERROR')
                warning_box.label(text=f"位置: {format_vector(obj.location)}")
                warning_box.label(text=f"旋转: {format_vector(obj.rotation_euler)}")
        else:
            info_box.label(text="请选择骨架对象", icon='ERROR')

        # Add a section for the new post-processing tool
        tool_box = layout.box()
        tool_box.label(text="动作后期处理", icon='TOOL_SETTINGS')
        # Add the operator button
        tool_box.operator(ANIM_OT_ApplyProgressiveOffset.bl_idname, icon='IPO_CONSTANT', text="应用渐进位移")
        # Add the new operator button
        tool_box.operator(ANIM_OT_ApplyFixedOrientation.bl_idname, icon='ORIENTATION_GIMBAL', text="应用固定朝向")

# 检查更新
def check_for_updates(auto_check=True):

    global last_update_check, update_available, latest_version, download_url, update_message
    
    # 如果是自动检查且上次检查时间未超过间隔，则跳过
    current_time = time.time()
    if auto_check and (current_time - last_update_check < UPDATE_CHECK_INTERVAL):
        return
    
    last_update_check = current_time
    
    # 获取当前版本
    current_version = bl_info['version']
    
    # 创建一个线程进行后台检查，避免阻塞主线程
    def background_check():
        global update_available, latest_version, download_url, update_message
        try:
            # 禁用SSL证书验证（仅用于测试环境）
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            
            # 发送请求检查更新
            print(f"正在连接更新服务器: {UPDATE_SERVER}/check_update")
            response = urllib.request.urlopen(f"{UPDATE_SERVER}/check_update", context=ctx, timeout=5)
            data = json.loads(response.read().decode('utf-8'))
            print(f"收到更新信息: {data}")
            
            server_version = tuple(data.get('version', (1, 8, 12)))
            latest_version = server_version
            update_message = data.get('message', '')
            download_url = data.get('download_url', '')
            
            # 比较版本号
            update_available = False
            for i in range(min(len(current_version), len(server_version))):
                if server_version[i] > current_version[i]:
                    update_available = True
                    break
                elif server_version[i] < current_version[i]:
                    break
            
            if update_available:
                print(f"发现新版本: {'.'.join(map(str, latest_version))}")
            else:
                print("当前已是最新版本")
                
        except urllib.error.URLError as e:
            print(f"无法连接到更新服务器: {str(e)}")
        except json.JSONDecodeError as e:
            print(f"解析服务器响应失败: {str(e)}")
        except Exception as e:
            print(f"检查更新时出错: {str(e)}")
    
    # 启动后台线程
    update_thread = threading.Thread(target=background_check)
    update_thread.daemon = True
    update_thread.start()

# 下载并安装更新
def download_and_install_update():

    global download_url
    
    if not download_url:
        return {'CANCELLED'}, "无法获取下载链接"
    
    try:
        # 创建临时目录下载更新
        temp_dir = tempfile.mkdtemp()
        temp_file = os.path.join(temp_dir, "update.py")
        
        # 禁用SSL证书验证（仅用于测试环境）
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        # 下载更新文件
        print(f"正在下载更新: {download_url}")
        opener = urllib.request.build_opener()
        opener.addheaders = [('User-agent', f'Blender/{bpy.app.version_string} FBXAnimImporter/{".".join(map(str, bl_info["version"]))}')]
        urllib.request.install_opener(opener)
        urllib.request.urlretrieve(download_url, temp_file)
        
        # 检查下载的文件
        if os.path.getsize(temp_file) == 0:
            raise ValueError("下载的文件为空")
            
        # 验证下载的文件是否是有效的Python脚本
        with open(temp_file, 'r', encoding='utf-8') as f:
            content = f.read()
            if not "bl_info" in content or not "register" in content:
                raise ValueError("下载的文件不是有效的Blender插件")
        
        # 获取当前脚本路径
        current_file = os.path.abspath(__file__)
        print(f"当前插件路径: {current_file}")
        
        # 备份当前文件
        backup_file = current_file + ".bak"
        shutil.copy2(current_file, backup_file)
        print(f"已备份原文件至: {backup_file}")
        
        # 替换当前文件
        shutil.copy2(temp_file, current_file)
        print(f"已更新插件文件: {current_file}")
        
        # 清理临时文件
        shutil.rmtree(temp_dir)
        
        # 强制重新加载当前文件
        try:
            bpy.ops.script.reload()
            print("尝试重新加载脚本")
        except:
            pass
        
        return {'FINISHED'}, "更新已下载，请重启Blender以应用更新。重启前请先保存您的工作！"
    except urllib.error.URLError as e:
        return {'CANCELLED'}, f"下载失败: 无法连接到服务器 ({str(e)})"
    except PermissionError:
        return {'CANCELLED'}, "更新失败: 无法写入文件，权限不足"
    except ValueError as e:
        return {'CANCELLED'}, f"更新失败: {str(e)}"
    except Exception as e:
        # 如果出现异常，尝试恢复备份
        try:
            backup_file = os.path.abspath(__file__) + ".bak"
            if os.path.exists(backup_file):
                shutil.copy2(backup_file, os.path.abspath(__file__))
                print("发生错误，已从备份恢复")
        except:
            pass
        return {'CANCELLED'}, f"更新失败: {str(e)}"

class ANIM_OT_CheckForUpdates(bpy.types.Operator):
    bl_idname = "anim.check_for_updates"
    bl_label = "检查更新"
    bl_description = "检查插件更新"
    
    def execute(self, context):
        check_for_updates(auto_check=False)
        self.report({'INFO'}, "更新检查已在后台启动，稍后将显示结果")
        return {'FINISHED'}

class ANIM_OT_InstallUpdate(bpy.types.Operator):
    bl_idname = "anim.install_update"
    bl_label = "安装更新"
    bl_description = "下载并安装插件更新"
    
    def execute(self, context):
        result, message = download_and_install_update()
        self.report({'INFO'}, message)
        return result

class ANIM_PT_UpdatePanel(bpy.types.Panel):
    bl_label = "更新与信息"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "动画工具"
    
    @classmethod
    def poll(cls, context):
        return context.scene.anim_merge_props.active_panel == 'UPDATE'
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.anim_merge_props
        
        # 当前版本
        version_str = '.'.join(map(str, bl_info['version']))
        box = layout.box()
        box.label(text=f"当前版本: {version_str}", icon='BLENDER')
        box.prop(props, "auto_check_update", icon='URL')
        
        # 更新状态
        if update_available and latest_version:
            latest_ver_str = '.'.join(map(str, latest_version))
            update_box = layout.box()
            update_box.alert = True
            update_box.label(text=f"发现新版本: {latest_ver_str}", icon='FILE_TICK')
            
            if update_message:
                message_box = update_box.box()
                message_box.scale_y = 0.7
                message_box.label(text="更新说明:")
                for line in update_message.split('\n'):
                    message_box.label(text=line)
            
            update_row = update_box.row(align=True)
            update_row.scale_y = 1.5
            update_row.operator("anim.install_update", text="下载并安装更新", icon='IMPORT')
        else:
            layout.label(text="当前已是最新版本" if last_update_check > 0 else "尚未检查更新")
        
        # 手动检查按钮
        check_row = layout.row(align=True)
        check_row.scale_y = 1.2
        check_row.operator("anim.check_for_updates", text="手动检查更新", icon='FILE_REFRESH')
        
        # 上次检查时间
        if last_update_check > 0:
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_update_check))
            layout.label(text=f"上次检查: {time_str}", icon='TIME')
            
        # 关于信息
        about_box = layout.box()
        about_box.label(text="关于插件", icon='HELP')
        about_box.label(text=f"作者: {bl_info['author']}")
        about_box.label(text=f"适用于Blender {bl_info['blender'][0]}.{bl_info['blender'][1]}.{bl_info['blender'][2]}+")
        about_box.label(text=bl_info['description'])

class ANIM_OT_ListFBXBones(bpy.types.Operator):

    bl_idname = "anim.list_fbx_bones"
    bl_label = "获取FBX骨骼列表"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        props = context.scene.anim_merge_props
        
        # 如果使用库文件
        if props.is_from_library:
            if not props.action_library_items:
                self.report({'ERROR'}, "未选择动作库中的动作")
                return {'CANCELLED'}
            item = props.action_library_items[props.action_library_index]
            filepath = Path(item.filepath)
            
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    action_data = json.load(f)
                
                # 提取骨骼名称
                bone_names = set()
                for fcurve in action_data.get('fcurves', []):
                    data_path = fcurve.get('data_path', '')
                    match = re.search(r'pose\.bones\["([^"]+)"\]', data_path)
                    if match:
                        bone_names.add(match.group(1))
                
                if not bone_names:
                    self.report({'INFO'}, "未找到骨骼，可能是物体级别的动画")
                    return {'CANCELLED'}
                
                # 保存骨骼列表
                context.scene['fbx_bones'] = list(bone_names)
                
                # 向用户报告
                self.report({'INFO'}, f"已找到 {len(bone_names)} 个骨骼")
                
                # 显示骨骼列表的对话框
                bpy.ops.anim.show_bones_dialog('INVOKE_DEFAULT')
                return {'FINISHED'}
            except Exception as e:
                self.report({'ERROR'}, f"读取动作文件失败: {str(e)}")
                return {'CANCELLED'}
        
        # 如果使用FBX文件
        else:
            if not props.filepath:
                self.report({'ERROR'}, "未选择FBX文件")
                return {'CANCELLED'}
            
            # 临时导入FBX以获取骨骼列表
            original_objects = set(bpy.context.scene.objects)
            try:
                bpy.ops.import_scene.fbx(
                    filepath=props.filepath,
                    use_anim=True,
                    automatic_bone_orientation=True,
                    ignore_leaf_bones=True
                )
            except Exception as e:
                self.report({'ERROR'}, f"FBX导入失败: {str(e)}")
                return {'CANCELLED'}
            
            # 找出新导入的物体
            new_objects = set(bpy.context.scene.objects) - original_objects
            armatures = [obj for obj in new_objects if obj.type == 'ARMATURE']
            
            if not armatures:
                # 清理导入的物体
                for obj in new_objects:
                    bpy.data.objects.remove(obj, do_unlink=True)
                self.report({'ERROR'}, "FBX文件中没有骨架")
                return {'CANCELLED'}
            
            # 第一个骨架
            armature = armatures[0]
            
            # 获取骨骼名称
            bone_names = [bone.name for bone in armature.data.bones]
            
            # 保存骨骼列表
            context.scene['fbx_bones'] = bone_names
            
            # 清理导入的物体
            for obj in new_objects:
                bpy.data.objects.remove(obj, do_unlink=True)
            
            # 向用户报告
            self.report({'INFO'}, f"已找到 {len(bone_names)} 个骨骼")
            
            # 显示骨骼列表的对话框
            bpy.ops.anim.show_bones_dialog('INVOKE_DEFAULT')
            return {'FINISHED'}

class ANIM_OT_ShowBonesDialog(bpy.types.Operator):

    bl_idname = "anim.show_bones_dialog"
    bl_label = "FBX骨骼列表"
    bl_options = {'REGISTER', 'UNDO'}
    
    def draw(self, context):
        layout = self.layout
        layout.label(text="FBX文件中的骨骼:", icon='BONE_DATA')
        
        # 获取骨骼列表
        bone_names = context.scene.get('fbx_bones', [])
        
        if bone_names:
            box = layout.box()
            col = box.column(align=True)
            
            for bone_name in sorted(bone_names):
                row = col.row()
                row.label(text=bone_name)
                copy_op = row.operator("anim.copy_bone_name", text="", icon='COPYDOWN')
                copy_op.bone_name = bone_name
        else:
            layout.label(text="未找到骨骼", icon='ERROR')
        
        # 使用提示
        help_box = layout.box()
        help_box.label(text="使用提示:", icon='INFO')
        help_box.label(text="1. 点击骨骼名称旁的复制按钮")
        help_box.label(text="2. 在骨骼映射界面中粘贴到'映射到'输入框")
        
    def execute(self, context):
        return {'FINISHED'}
        
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=400)

class ANIM_OT_CopyBoneName(bpy.types.Operator):

    bl_idname = "anim.copy_bone_name"
    bl_label = "复制骨骼名称"
    
    bone_name: bpy.props.StringProperty()
    
    def execute(self, context):
        context.window_manager.clipboard = self.bone_name
        self.report({'INFO'}, f"已复制: {self.bone_name}")
        return {'FINISHED'}

class ANIM_OT_SearchBone(bpy.types.Operator):

    bl_idname = "anim.search_bone"
    bl_label = "搜索目标骨骼"
    bl_options = {'REGISTER', 'UNDO'}
    
    # 存储当前编辑的骨骼项索引
    bone_index: bpy.props.IntProperty()
    
    # 搜索文本
    bone_search_text: bpy.props.StringProperty(
        name="搜索",
        description="输入骨骼名称进行搜索",
        update=lambda self, context: self.update_search(context)
    )
    
    # 当前选中的骨骼
    selected_bone: bpy.props.StringProperty(default="")
    
    def update_search(self, context):

        self.selected_bone = ""
    
    def get_bone_items(self, context):

        items = []
        
        # 获取当前骨架
        active_obj = context.active_object
        if active_obj and active_obj.type == 'ARMATURE':
            # 获取搜索文本
            search_text = self.bone_search_text.lower()
            
            # 搜索所有匹配的骨骼
            bone_names = []
            for bone in active_obj.data.bones:
                if search_text in bone.name.lower():
                    bone_names.append(bone.name)
            
            # 按字母排序
            bone_names.sort()
            
            # 转换为选项列表
            for name in bone_names:
                items.append((name, name, f"选择 {name}"))
        
        # 如果没有匹配的骨骼，添加一个提示
        if not items:
            items.append(("", "没有匹配的骨骼", ""))
            
        return items
    
    bone_list: bpy.props.EnumProperty(
        name="匹配骨骼",
        description="符合搜索条件的骨骼列表",
        items=get_bone_items
    )
    
    def execute(self, context):
        if self.selected_bone:
            # 将选中的骨骼名称应用到目标映射字段
            if self.bone_index < len(context.scene.bone_selection):
                item = context.scene.bone_selection[self.bone_index]
                item.mapped_to = self.selected_bone
                self.report({'INFO'}, f"已将骨骼 '{item.name}' 映射到 '{self.selected_bone}'")
                return {'FINISHED'}
        return {'CANCELLED'}
    
    def invoke(self, context, event):
        # 清空搜索文本和选择
        self.bone_search_text = ""
        self.selected_bone = ""
        return context.window_manager.invoke_props_dialog(self, width=300)
    
    def draw(self, context):
        layout = self.layout
        
        # 搜索框
        search_row = layout.row()
        search_row.prop(self, "bone_search_text", icon='VIEWZOOM')
        
        # 获取匹配的骨骼数量
        matched_bones = 0
        active_obj = context.active_object
        if active_obj and active_obj.type == 'ARMATURE':
            search_text = self.bone_search_text.lower()
            for bone in active_obj.data.bones:
                if search_text in bone.name.lower():
                    matched_bones += 1
        
        # 显示匹配信息
        if matched_bones > 0:
            layout.label(text=f"找到 {matched_bones} 个匹配骨骼")
        else:
            layout.label(text="没有找到匹配的骨骼", icon='ERROR')
        
        # 骨骼列表
        box = layout.box()
        col = box.column()
        
        # 显示匹配的骨骼
        active_obj = context.active_object
        if active_obj and active_obj.type == 'ARMATURE':
            search_text = self.bone_search_text.lower()
            
            # 获取并排序匹配的骨骼
            matching_bones = []
            for bone in active_obj.data.bones:
                if search_text in bone.name.lower():
                    matching_bones.append(bone.name)
            
            matching_bones.sort()
            
            # 显示骨骼按钮
            for bone_name in matching_bones:
                row = col.row()
                op = row.operator("anim.select_searched_bone", text=bone_name)
                op.bone_name = bone_name
                op.bone_index = self.bone_index
        
        # 显示当前选中的骨骼
        if self.selected_bone:
            selected_row = layout.row()
            selected_row.label(text=f"已选择: {self.selected_bone}", icon='CHECKMARK')

class ANIM_OT_SelectBoneItem(bpy.types.Operator):

    bl_idname = "anim.select_bone_item"
    bl_label = "选择骨骼项"
    
    bone_name: bpy.props.StringProperty()
    
    def execute(self, context):
        # 直接设置搜索操作符的selected_bone值
        wm = context.window_manager
        
        # 通过标签找到我们的搜索操作符
        for op in wm.operators:
            if op.bl_idname == "ANIM_OT_SearchBone":
                op.selected_bone = self.bone_name
                self.report({'INFO'}, f"已选择: {self.bone_name}")
                return {'FINISHED'}
        
        # 如果找不到操作符，则更新骨骼映射操作符的属性
        if hasattr(context, 'active_operator') and hasattr(context.active_operator, 'selected_bone'):
            context.active_operator.selected_bone = self.bone_name
        
        return {'FINISHED'}

class ANIM_OT_ConfirmBoneSelection(bpy.types.Operator):

    bl_idname = "anim.confirm_bone_selection"
    bl_label = "确认骨骼选择"
    
    bone_name: bpy.props.StringProperty()
    bone_index: bpy.props.IntProperty()
    
    def execute(self, context):
        if self.bone_name and self.bone_index < len(context.scene.bone_selection):
            item = context.scene.bone_selection[self.bone_index]
            item.mapped_to = self.bone_name
            self.report({'INFO'}, f"已将骨骼 '{item.name}' 映射到 '{self.bone_name}'")
        return {'FINISHED'}

# 添加一个新的操作符用于选择搜索到的骨骼
class ANIM_OT_SelectSearchedBone(bpy.types.Operator):

    bl_idname = "anim.select_searched_bone"
    bl_label = "选择搜索到的骨骼"
    
    bone_name: bpy.props.StringProperty()
    bone_index: bpy.props.IntProperty()
    
    def execute(self, context):
        # 直接应用映射，不需要经过其他操作符
        if self.bone_index < len(context.scene.bone_selection):
            item = context.scene.bone_selection[self.bone_index]
            item.mapped_to = self.bone_name
            self.report({'INFO'}, f"已将骨骼 '{item.name}' 映射到 '{self.bone_name}'")
            
            # 关闭对话框
            return {'FINISHED'}
        
        return {'CANCELLED'}

# 计算字符串的相似度得分
def string_similarity(s1, s2):

    # 转为小写进行比较
    s1, s2 = s1.lower(), s2.lower()
    
    # 完全匹配
    if s1 == s2:
        return 1.0
    
    # 去除常见的前缀和后缀再比较
    prefixes = ['bone_', 'b_', 'bone.', 'bones.']
    suffixes = ['.l', '.r', '_l', '_r', '_left', '_right', '.left', '.right']
    
    s1_clean = s1
    s2_clean = s2
    
    # 处理前缀
    for prefix in prefixes:
        if s1.startswith(prefix):
            s1_clean = s1[len(prefix):]
        if s2.startswith(prefix):
            s2_clean = s2[len(prefix):]
    
    # 处理后缀
    for suffix in suffixes:
        if s1_clean.endswith(suffix):
            s1_clean = s1_clean[:-len(suffix)]
        if s2_clean.endswith(suffix):
            s2_clean = s2_clean[:-len(suffix)]
    
    # 如果清洗后完全匹配
    if s1_clean == s2_clean:
        return 0.9  # 给一个较高但不是完全匹配的分数
    
    # 如果一个是另一个的子串
    if s1_clean in s2_clean or s2_clean in s1_clean:
        return 0.8
    
    # 计算编辑距离
    len_s1, len_s2 = len(s1_clean), len(s2_clean)
    if len_s1 == 0 or len_s2 == 0:
        return 0.0
        
    # 莱文斯坦距离简化版本
    distance = 0
    for i, c1 in enumerate(s1_clean):
        if i < len_s2 and c1 == s2_clean[i]:
            continue
        distance += 1
    
    # 归一化距离
    similarity = 1.0 - (distance / max(len_s1, len_s2))
    return max(0.0, similarity)

# 获取FBX骨骼位置信息
def get_fbx_bone_positions(filepath):

    positions = {}
    
    # 临时导入FBX
    original_objects = set(bpy.context.scene.objects)
    try:
        bpy.ops.import_scene.fbx(
            filepath=filepath,
            use_anim=True,
            automatic_bone_orientation=True,
            ignore_leaf_bones=True
        )
        
        # 找出新导入的物体
        new_objects = set(bpy.context.scene.objects) - original_objects
        armatures = [obj for obj in new_objects if obj.type == 'ARMATURE']
        
        if armatures:
            # 使用第一个骨架
            armature = armatures[0]
            
            # 获取每个骨骼的全局位置
            for bone in armature.data.bones:
                # 获取骨骼头部的全局坐标
                head_pos = armature.matrix_world @ bone.head_local
                positions[bone.name] = {
                    'head': head_pos.copy(),
                    'parent': bone.parent.name if bone.parent else None
                }
        
        # 清理导入的物体
        for obj in new_objects:
            bpy.data.objects.remove(obj, do_unlink=True)
            
    except Exception as e:
        print(f"获取FBX骨骼位置失败: {str(e)}")
    
    return positions

# 从JSON文件中获取骨骼信息 
def get_json_bone_names(filepath):

    bone_names = set()
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            action_data = json.load(f)
        
        # 提取骨骼名称
        for fcurve in action_data.get('fcurves', []):
            data_path = fcurve.get('data_path', '')
            match = re.search(r'pose\.bones\["([^"]+)"\]', data_path)
            if match:
                bone_names.add(match.group(1))
                
    except Exception as e:
        print(f"从JSON获取骨骼名称失败: {str(e)}")
    
    return bone_names

# 自动预测骨骼映射
def predict_bone_mapping(context, source_bones, target_armature):

    if not source_bones or not target_armature:
        return {}
    
    # 获取目标骨架的所有骨骼
    target_bones = [bone.name for bone in target_armature.data.bones]
    
    # 创建映射
    mapping = {}
    
    # 首先基于名称进行精确匹配
    for source_bone in source_bones:
        if source_bone in target_bones:
            mapping[source_bone] = source_bone
            
    # 然后基于名称相似度匹配剩余的骨骼
    unmapped_sources = [b for b in source_bones if b not in mapping]
    
    for source_bone in unmapped_sources:
        best_match = None
        best_score = 0.5  # 设置阈值，避免不相关的匹配
        
        for target_bone in target_bones:
            # 计算相似度
            similarity = string_similarity(source_bone, target_bone)
            
            if similarity > best_score:
                best_score = similarity
                best_match = target_bone
        
        if best_match:
            mapping[source_bone] = best_match
    
    # 对于特定的左右对称骨骼进行特殊处理
    for source_bone in unmapped_sources:
        if source_bone not in mapping:
            # 处理左侧和右侧的命名规则
            if any(suffix in source_bone.lower() for suffix in ['left', '_l', '.l']):
                # 寻找可能的右侧对应骨骼
                source_right = source_bone.lower().replace('left', 'right').replace('_l', '_r').replace('.l', '.r')
                if source_right in mapping:
                    # 如果右侧有映射，尝试将左侧匹配到相应的左侧骨骼
                    right_target = mapping[source_right]
                    left_target = right_target.lower().replace('right', 'left').replace('_r', '_l').replace('.r', '.l')
                    # 确认左侧目标骨骼存在
                    if left_target in target_bones:
                        mapping[source_bone] = left_target
    
    return mapping

# 添加用于自动骨骼映射的操作符
class ANIM_OT_AutoBoneMapping(bpy.types.Operator):

    bl_idname = "anim.auto_bone_mapping"
    bl_label = "自动骨骼映射"
    bl_options = {'REGISTER', 'UNDO'}
    
    match_threshold: bpy.props.FloatProperty(
        name="匹配阈值",
        description="骨骼名称相似度匹配的阈值",
        default=0.6,
        min=0.1,
        max=1.0
    )
    
    def execute(self, context):
        props = context.scene.anim_merge_props
        target_armature = context.active_object
        
        if not target_armature or target_armature.type != 'ARMATURE':
            self.report({'ERROR'}, "请先选择目标骨架")
            return {'CANCELLED'}
        
        source_bones = []
        
        # 从骨骼选择列表中获取源骨骼
        for item in context.scene.bone_selection:
            if item.selected:
                source_bones.append(item.name)
        
        if not source_bones:
            self.report({'ERROR'}, "没有选择任何源骨骼")
            return {'CANCELLED'}
        
        # 预测骨骼映射
        mapping = predict_bone_mapping(context, source_bones, target_armature)
        
        if not mapping:
            self.report({'WARNING'}, "无法预测任何骨骼映射关系")
            return {'CANCELLED'}
        
        # 将预测的映射应用到骨骼选择列表
        count = 0
        for item in context.scene.bone_selection:
            if item.name in mapping and item.selected:
                item.mapped_to = mapping[item.name]
                item.use_mapping = True
                count += 1
        
        self.report({'INFO'}, f"成功预测并应用了 {count} 个骨骼映射")
        return {'FINISHED'}
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)
    
    def draw(self, context):
        layout = self.layout
        layout.prop(self, "match_threshold")
        layout.label(text="这将根据骨骼名称相似度自动预测映射关系", icon='INFO')

# Operator for applying progressive offset
class ANIM_OT_ApplyProgressiveOffset(bpy.types.Operator):

    bl_idname = "anim.apply_progressive_offset"
    bl_label = "应用渐进位移"
    bl_options = {'REGISTER', 'UNDO'}

    start_frame: bpy.props.IntProperty(
        name="起始帧",
        description="应用偏移的起始帧",
        default=1,
        min=0
    )
    end_frame: bpy.props.IntProperty(
        name="结束帧",
        description="应用偏移的结束帧",
        default=100,
        min=1
    )
    offset_per_frame: bpy.props.FloatProperty(
        name="每帧偏移量",
        description="在朝向方向上，每帧增加的位移量",
        default=0.01,
        unit='LENGTH'
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'ARMATURE' and obj.animation_data and obj.animation_data.action

    def invoke(self, context, event):
        action = context.active_object.animation_data.action
        # Try to guess a reasonable default range from the action
        if action and action.frame_range and action.frame_range[1] > action.frame_range[0]:
            self.start_frame = int(action.frame_range[0])
            self.end_frame = int(action.frame_range[1])
        else:
             # Fallback if no action or invalid range
             self.start_frame = context.scene.frame_start
             self.end_frame = context.scene.frame_end

        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        armature = context.active_object
        action = armature.animation_data.action

        if not action:
            self.report({'ERROR'}, "目标骨架没有活动Action")
            return {'CANCELLED'}

        start_frame = self.start_frame
        end_frame = self.end_frame
        offset_per_frame = self.offset_per_frame

        if start_frame >= end_frame:
            self.report({'ERROR'}, "起始帧必须小于结束帧")
            return {'CANCELLED'}

        # --- Determine Forward Vector at the *Action's* Start Frame ---
        current_frame = context.scene.frame_current
        # Determine the frame to use for orientation (Action start or frame 1)
        orientation_frame = int(action.frame_range[0]) if action and action.frame_range and action.frame_range[0] is not None else 1

        forward_vector = mathutils.Vector((0.0, 1.0, 0.0)) # Default to world Y
        try:
            print(f"Attempting to set frame to {orientation_frame} for orientation calculation.")
            # Set frame specifically to get the initial orientation
            context.scene.frame_set(orientation_frame)
            context.view_layer.update() # Ensure scene updates for matrix

            world_matrix = armature.matrix_world.copy()
            # Assuming Local +Y is the forward direction for objects in Blender
            forward_vector_candidate = world_matrix.col[1].xyz
            # Check if the vector has non-zero length before normalizing
            if forward_vector_candidate.length_squared > 0.000001: # Use length_squared for efficiency
                 forward_vector = forward_vector_candidate.normalized()
            else:
                 # This might happen if scale is zero or object is perfectly aligned in a weird way
                 self.report({'WARNING'}, "无法确定骨架朝向 (可能缩放为0?), 使用世界+Y轴代替")
                 # Keep the default world Y vector

            # Update print statement to reflect which frame was used
            print(f"Orientation from Frame {orientation_frame}: Forward Vector = {format_vector(forward_vector)}")

        except Exception as e:
             self.report({'ERROR'}, f"获取帧 {orientation_frame} 状态时出错: {e}")
             # Restore frame before returning
             context.scene.frame_set(current_frame)
             context.view_layer.update()
             return {'CANCELLED'}
        finally:
            # Always restore original frame
            context.scene.frame_set(current_frame)
            context.view_layer.update()

        # --- Find Location F-Curves for the Armature Object ---
        # We target the object's location curves by default
        loc_fcurves = {
            fc.array_index: fc
            for fc in action.fcurves
            if fc.data_path == 'location'
        }

        if len(loc_fcurves) != 3:
            self.report({'ERROR'}, "未找到完整的对象位置 F-Curves (X, Y, Z)。此功能当前仅支持修改对象本身的位移动画。")
            # Future improvement: Add option to target a specific root bone's location
            return {'CANCELLED'}

        # --- Collect Keyframes in Range ---
        # Stores data for frames that have at least one location keyframe within the range
        # {frame_int: {'kp': [kp_x, kp_y, kp_z], 'original_value': [x, y, z]}}
        keyframes_to_modify = {}
        min_frame_in_range = float('inf')
        max_frame_in_range = float('-inf')

        for axis_index, fcurve in loc_fcurves.items():
            if not fcurve.keyframe_points:
                 continue # Skip curves with no keyframes

            for kp in fcurve.keyframe_points:
                # Use the keyframe's actual frame number (float) for range check
                # but group by rounded integer frame for processing
                if start_frame <= kp.co.x <= end_frame:
                    frame_int = int(round(kp.co.x))
                    min_frame_in_range = min(min_frame_in_range, frame_int)
                    max_frame_in_range = max(max_frame_in_range, frame_int)

                    if frame_int not in keyframes_to_modify:
                        # Initialize structure for this frame
                        keyframes_to_modify[frame_int] = {
                            'kp': [None, None, None],
                            'original_value': [None, None, None] # Use None to detect missing keys later
                        }

                    # Store the keyframe point object itself
                    keyframes_to_modify[frame_int]['kp'][axis_index] = kp
                    # Store the original value directly from the keyframe point
                    keyframes_to_modify[frame_int]['original_value'][axis_index] = kp.co.y

        # --- Apply Progressive Offset ---
        sorted_frames = sorted(keyframes_to_modify.keys())

        if not sorted_frames:
             self.report({'INFO'}, f"在指定的帧范围 [{start_frame}-{end_frame}] 内未找到对象位置关键帧。")
             return {'FINISHED'} # No work to do, but not an error

        # Use the first actual keyframe *found within the user-specified range* as the zero point for progress calculation.
        # This ensures the offset starts correctly even if the user range doesn't start at the very first keyframe.
        actual_start_frame_for_offset = min_frame_in_range
        modified_count = 0

        print(f"Processing {len(sorted_frames)} frames with keyframes between {min_frame_in_range} and {max_frame_in_range}.")
        print(f"Using frame {actual_start_frame_for_offset} as baseline for progressive offset.")

        for frame_int in sorted_frames:
            data = keyframes_to_modify[frame_int]

            # Check if we have keyframes and original values for all 3 axes at this frame
            if any(kp is None for kp in data['kp']) or any(val is None for val in data['original_value']):
                print(f"Skipping frame {frame_int}: Missing keyframe or original value on one or more axes.")
                # Find which axis is missing for better debugging
                missing_axes = [i for i, kp in enumerate(data['kp']) if kp is None]
                print(f"  Missing key points on axes: {missing_axes}")
                missing_vals = [i for i, val in enumerate(data['original_value']) if val is None]
                print(f"  Missing original values on axes: {missing_vals}")
                continue # Skip this frame if data is incomplete

            # Now safe to create the vector
            original_loc = mathutils.Vector(data['original_value'])

            # Calculate progress relative to the first keyframe *found in the range*
            progress = frame_int - actual_start_frame_for_offset
            # Ensure progress is not negative if actual_start_frame_for_offset is somehow greater (shouldn't happen with sorting)
            if progress < 0: progress = 0

            # Calculate the offset vector for this frame
            offset = forward_vector * offset_per_frame * progress

            # Calculate the new location
            new_loc = original_loc + offset

            # Print occasional debug info for verification
            if frame_int == sorted_frames[0] or frame_int == sorted_frames[-1] or frame_int % 20 == 0: # Adjust frequency as needed
                 print(f"  Frame {frame_int}: Progress={progress:.2f}, Offset={format_vector(offset)}, Orig={format_vector(original_loc)}, New={format_vector(new_loc)}")

            # Apply the new location to the keyframe points for X, Y, Z
            key_modified_this_frame = False
            for axis_index in range(3):
                kp = data['kp'][axis_index]
                # Double check kp exists (already checked above, but belt-and-suspenders)
                if kp:
                    # Only update if the value actually changed significantly? Optional.
                    # if abs(kp.co.y - new_loc[axis_index]) > 0.0001:
                    kp.co.y = new_loc[axis_index]
                    key_modified_this_frame = True

            if key_modified_this_frame:
                modified_count += 1 # Count frames where at least one key was updated

        # --- Update Curves ---
        if modified_count > 0:
            print(f"Updating {len(loc_fcurves)} F-curves...")
            for fcurve in loc_fcurves.values():
                try:
                    # This ensures Blender recognizes the changes
                    fcurve.update()
                except Exception as e:
                    # Report error but try to continue updating others
                    print(f"Error updating fcurve {fcurve.data_path}[{fcurve.array_index}]: {e}")
                    self.report({'ERROR'}, f"更新曲线时出错: {e}")

            self.report({'INFO'}, f"已在 {modified_count} 个关键帧上应用渐进位移 (检测范围 {min_frame_in_range}-{max_frame_in_range})")
        else:
            # This case might be hit if offset_per_frame is 0 or keyframes existed but weren't modified
            self.report({'INFO'}, "未修改任何关键帧 (可能范围内无关键帧、偏移量为0或数值无变化)")


        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "start_frame")
        layout.prop(self, "end_frame")
        layout.prop(self, "offset_per_frame")

        info_box = layout.box()
        info_box.label(text="说明:", icon='INFO')
        info_box.label(text="- 此操作会直接修改选中骨架当前Action的对象位置关键帧。")
        info_box.label(text="- 偏移量从指定范围内的第一个关键帧开始，逐帧累加。")
        # Update help text to clarify the orientation source
        info_box.label(text="- 偏移方向基于 Action第一帧 (或帧1) 时骨架对象的局部+Y轴在世界空间中的朝向。")
        info_box.label(text="- 仅处理在指定范围内的现有对象位置关键帧。")
        info_box.label(text="- 请确保在运行前将时间滑块移出修改范围，以避免预览冲突。")


# End of ANIM_OT_ApplyProgressiveOffset class definition

# Operator for applying fixed orientation
class ANIM_OT_ApplyFixedOrientation(bpy.types.Operator):

    bl_idname = "anim.apply_fixed_orientation"
    bl_label = "应用固定朝向"
    bl_options = {'REGISTER', 'UNDO'}

    start_frame: bpy.props.IntProperty(
        name="起始帧",
        description="应用固定朝向的起始帧",
        default=1,
        min=0
    )
    end_frame: bpy.props.IntProperty(
        name="结束帧",
        description="应用固定朝向的结束帧",
        default=100,
        min=1
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'ARMATURE' and obj.animation_data and obj.animation_data.action

    def invoke(self, context, event):
        action = context.active_object.animation_data.action
        # Try to guess a reasonable default range
        if action and action.frame_range and action.frame_range[1] > action.frame_range[0]:
            self.start_frame = int(action.frame_range[0])
            self.end_frame = int(action.frame_range[1])
        else:
            self.start_frame = context.scene.frame_start
            self.end_frame = context.scene.frame_end
        # Ensure start frame is at least 1 if action range is weird
        self.start_frame = max(1, self.start_frame)

        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        armature = context.active_object
        action = armature.animation_data.action

        if not action:
            self.report({'ERROR'}, "目标骨架没有活动Action")
            return {'CANCELLED'}

        start_frame_user = self.start_frame
        end_frame_user = self.end_frame

        if start_frame_user >= end_frame_user:
            self.report({'ERROR'}, "起始帧必须小于结束帧")
            return {'CANCELLED'}

        # --- Determine Orientation at the Action's Start Frame ---
        current_frame = context.scene.frame_current
        orientation_frame = int(action.frame_range[0]) if action and action.frame_range and action.frame_range[0] is not None else 1
        target_world_rotation_quat = None

        try:
            print(f"Attempting to set frame to {orientation_frame} for initial orientation capture.")
            context.scene.frame_set(orientation_frame)
            context.view_layer.update()

            # Capture world rotation as Quaternion
            target_world_rotation_quat = armature.matrix_world.to_quaternion()
            print(f"Captured orientation (Quaternion) from Frame {orientation_frame}: {target_world_rotation_quat}")

        except Exception as e:
            self.report({'ERROR'}, f"获取帧 {orientation_frame} 旋转状态时出错: {e}")
            context.scene.frame_set(current_frame)
            context.view_layer.update()
            return {'CANCELLED'}
        finally:
            # Restore original frame
            context.scene.frame_set(current_frame)
            context.view_layer.update()

        if target_world_rotation_quat is None:
             self.report({'ERROR'}, "未能获取初始旋转")
             return {'CANCELLED'}

        # --- Find Rotation F-Curves (Object Level) ---
        # Determine current rotation mode ONCE
        current_rotation_mode = armature.rotation_mode
        print(f"Armature rotation mode: {current_rotation_mode}")

        rot_fcurves_quat = {
            fc.array_index: fc
            for fc in action.fcurves
            if fc.data_path == 'rotation_quaternion'
        }
        rot_fcurves_euler = {
            fc.array_index: fc
            for fc in action.fcurves
            if fc.data_path == 'rotation_euler'
        }
        # We will modify curves based on current_rotation_mode

        # --- Collect Keyframes in User Range ---
        keyframes_to_modify = set()
        active_fcurves_to_update = set() # Track which curves actually get modified

        curves_to_check = list(rot_fcurves_quat.values()) + list(rot_fcurves_euler.values())

        for fcurve in curves_to_check:
            if not fcurve.keyframe_points:
                 continue
            for kp in fcurve.keyframe_points:
                # Check if the keyframe falls within the user-specified range
                # We modify from the start_frame_user onwards
                if start_frame_user <= kp.co.x <= end_frame_user:
                    keyframes_to_modify.add(int(round(kp.co.x)))

        # --- Apply Fixed Orientation ---
        sorted_frames = sorted(list(keyframes_to_modify))

        if not sorted_frames:
             self.report({'INFO'}, f"在指定的帧范围 [{start_frame_user}-{end_frame_user}] 内未找到对象旋转关键帧。")
             return {'FINISHED'}

        modified_count = 0
        print(f"Processing {len(sorted_frames)} frames with rotation keyframes between {sorted_frames[0]} and {sorted_frames[-1]}.")

        # --- Pre-calculate Target Rotation in Current Mode ---
        target_rotation_value = None
        if 'QUATERNION' in current_rotation_mode:
            target_rotation_value = target_world_rotation_quat.copy()
            print(f"Target Rotation (Quaternion): {target_rotation_value}")
        elif 'AXIS_ANGLE' in current_rotation_mode:
            # Axis angle needs careful handling - converting world quat to local axis angle is complex.
            # For simplicity, let's convert the target quat to Euler based on mode if AxisAngle is detected
            # Or better yet, force quaternion mode temporarily? Sticking to Euler for now if AxisAngle.
             try:
                target_rotation_value = target_world_rotation_quat.to_euler(current_rotation_mode)
                print(f"Target Rotation (converted to Euler {current_rotation_mode} from AxisAngle): {target_rotation_value}")
             except ValueError as e:
                self.report({'ERROR'}, f"无法将目标旋转转换为欧拉角（模式：{current_rotation_mode}）：{e}")
                return {'CANCELLED'}
        else: # Handle all Euler modes (XYZ, XZY, etc.)
            try:
                target_rotation_value = target_world_rotation_quat.to_euler(current_rotation_mode)
                print(f"Target Rotation (Euler {current_rotation_mode}): {target_rotation_value}")
            except ValueError as e:
                 self.report({'ERROR'}, f"无法将目标旋转转换为欧拉角（模式：{current_rotation_mode}）：{e}")
                 return {'CANCELLED'}

        if target_rotation_value is None:
            self.report({'ERROR'}, "无法计算目标旋转值")
            return {'CANCELLED'}

        # --- Modify Keyframes --- 
        for frame_int in sorted_frames:
            # Check if the frame is actually within the user range (redundant due to collection method, but safe)
            if not (start_frame_user <= frame_int <= end_frame_user):
                continue

            key_modified_this_frame = False

            if 'QUATERNION' in current_rotation_mode:
                # Ensure all 4 quaternion curves exist for this frame
                for i in range(4):
                    fcurve = rot_fcurves_quat.get(i)
                    if not fcurve:
                         # Create fcurve if it doesn't exist
                         fcurve = action.fcurves.new(data_path='rotation_quaternion', index=i)
                         rot_fcurves_quat[i] = fcurve
                         print(f"Created missing fcurve: rotation_quaternion[{i}]")

                    # Insert or update keyframe
                    # Use insert() which handles both cases
                    kp = fcurve.keyframe_points.insert(frame_int, target_rotation_value[i], options={'NEEDED', 'FAST'})
                    #kp.interpolation = 'LINEAR' # Or CONSTANT? Let's stick to Blender default for now
                    active_fcurves_to_update.add(fcurve)
                    key_modified_this_frame = True
                # Clean up Euler keys at this frame (optional but good practice)
                for i in range(3):
                    if i in rot_fcurves_euler:
                         kp_to_remove = rot_fcurves_euler[i].keyframe_points.find(frame_int)
                         if kp_to_remove:
                             rot_fcurves_euler[i].keyframe_points.remove(kp_to_remove)
                             print(f"Removed conflicting Euler key at frame {frame_int} axis {i}")
                             active_fcurves_to_update.add(rot_fcurves_euler[i])

            else: # Euler or AxisAngle (handled as Euler)
                # Ensure all 3 euler curves exist for this frame
                for i in range(3):
                    fcurve = rot_fcurves_euler.get(i)
                    if not fcurve:
                        fcurve = action.fcurves.new(data_path='rotation_euler', index=i)
                        rot_fcurves_euler[i] = fcurve
                        print(f"Created missing fcurve: rotation_euler[{i}]")

                    # Insert or update keyframe
                    kp = fcurve.keyframe_points.insert(frame_int, target_rotation_value[i], options={'NEEDED', 'FAST'})
                    active_fcurves_to_update.add(fcurve)
                    key_modified_this_frame = True
                # Clean up Quaternion keys at this frame
                for i in range(4):
                    if i in rot_fcurves_quat:
                        kp_to_remove = rot_fcurves_quat[i].keyframe_points.find(frame_int)
                        if kp_to_remove:
                            rot_fcurves_quat[i].keyframe_points.remove(kp_to_remove)
                            print(f"Removed conflicting Quaternion key at frame {frame_int} axis {i}")
                            active_fcurves_to_update.add(rot_fcurves_quat[i])

            if key_modified_this_frame:
                modified_count += 1

        # --- Update Curves ---
        if modified_count > 0:
            print(f"Updating {len(active_fcurves_to_update)} modified F-curves...")
            for fcurve in active_fcurves_to_update:
                 try:
                     fcurve.update()
                 except Exception as e:
                     print(f"Error updating fcurve {fcurve.data_path}[{fcurve.array_index}]: {e}")
                     self.report({'ERROR'}, f"更新曲线时出错: {e}")

            self.report({'INFO'}, f"已在 {modified_count} 个关键帧上应用固定朝向 (检测范围 {sorted_frames[0]}-{sorted_frames[-1]})")
        else:
             self.report({'INFO'}, "未修改任何旋转关键帧 (可能范围内无关键帧)")

        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "start_frame")
        layout.prop(self, "end_frame")

        info_box = layout.box()
        info_box.label(text="说明:", icon='INFO')
        info_box.label(text="- 此操作会直接修改选中骨架当前Action的对象旋转关键帧。")
        info_box.label(text="- 它会读取 Action第一帧(或帧1) 的骨架世界旋转。")
        info_box.label(text="- 然后将此旋转值应用到指定范围内的所有现有旋转关键帧上。")
        info_box.label(text="- 会根据骨架当前的旋转模式 (欧拉/四元数) 修改对应通道。")
        info_box.label(text="- 请确保在运行前将时间滑块移出修改范围。")

# End of ANIM_OT_ApplyFixedOrientation class definition

# Operator to delete a selected action from the library
class ANIM_OT_DeleteLibraryAction(bpy.types.Operator):

    bl_idname = "anim.delete_library_action"
    bl_label = "删除选中动作"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.anim_merge_props
        # Enable only if the list is not empty and an item is selected
        return props and props.action_library_items and props.action_library_index >= 0

    def execute(self, context):
        props = context.scene.anim_merge_props
        index_to_remove = props.action_library_index

        if not (0 <= index_to_remove < len(props.action_library_items)):
            self.report({'WARNING'}, "没有选中的有效动作项")
            return {'CANCELLED'}

        item_to_remove = props.action_library_items[index_to_remove]
        action_name = item_to_remove.name
        action_filepath_str = item_to_remove.filepath

        if not action_filepath_str:
            self.report({'ERROR'}, f"动作 '{action_name}' 没有有效的文件路径，无法删除")
            # Attempt to remove from list anyway?
            try:
                props.action_library_items.remove(index_to_remove)
                # Adjust index if it's now out of bounds
                props.action_library_index = min(index_to_remove, len(props.action_library_items) - 1)
                self.report({'INFO'}, f"已从列表移除无路径的动作 '{action_name}'")
            except Exception as e:
                self.report({'ERROR'}, f"从列表移除无路径动作时出错: {e}")
            return {'CANCELLED'}

        action_filepath = Path(action_filepath_str)

        # Confirmation dialog
        # Using invoke_confirm is generally better, but for simplicity using execute with report
        print(f"准备删除动作: {action_name} ({action_filepath})")

        # --- Delete the file --- 
        try:
            if action_filepath.is_file():
                os.remove(action_filepath)
                print(f"已删除文件: {action_filepath}")
            else:
                self.report({'WARNING'}, f"文件未找到，可能已被删除: {action_filepath}")
                # Proceed to remove from list even if file not found

            # --- Remove from Blender's collection --- 
            props.action_library_items.remove(index_to_remove)

            # --- Adjust the active index --- 
            # Try to keep selection reasonable, select previous item or first if it was the first
            new_index = min(index_to_remove, len(props.action_library_items) - 1)
            props.action_library_index = max(0, new_index) # Ensure index is not negative if list becomes empty
            if not props.action_library_items:
                 props.action_library_index = -1 # Indicate empty list


            self.report({'INFO'}, f"已删除动作: '{action_name}'")
            return {'FINISHED'}

        except OSError as e:
            self.report({'ERROR'}, f"删除文件 '{action_filepath.name}' 时出错: {e}")
            return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"删除动作时发生未知错误: {e}")
            return {'CANCELLED'}

    # Optional: Add invoke method for confirmation dialog
    # def invoke(self, context, event):
    #     return context.window_manager.invoke_confirm(self, event)


# Helper function to get library folders
def get_library_folders(self, context):
    items = []
    if LIBRARY_PATH.exists() and LIBRARY_PATH.is_dir():
        for item in LIBRARY_PATH.iterdir():
            if item.is_dir():
                items.append((item.name, item.name, f"目标分类 {item.name}"))
    if not items:
        items.append(("_NO_FOLDERS_", "没有可用的分类", "请先创建分类"))
    items.sort(key=lambda x: x[0].lower())
    return items

# Operator to create a new folder in the library
class ANIM_OT_CreateLibraryFolder(bpy.types.Operator):

    bl_idname = "anim.create_library_folder"
    bl_label = "创建新分类"
    bl_options = {'REGISTER', 'UNDO'}

    new_folder_name: bpy.props.StringProperty(
        name="分类名称",
        description="新分类（文件夹）的名称",
        default="新分类"
    )

    def execute(self, context):
        raw_name = self.new_folder_name.strip()
        if not raw_name:
            self.report({'ERROR'}, "分类名称不能为空")
            return {'CANCELLED'}

        cleaned_name_part = clean_filename(raw_name)
        if not cleaned_name_part or cleaned_name_part in [".", ".."]:
            self.report({'ERROR'}, "无效的分类名称部分")
            return {'CANCELLED'}

        safe_name = f"Group_{cleaned_name_part}"

        new_folder_path = LIBRARY_PATH / safe_name

        if new_folder_path.exists():
            self.report({'ERROR'}, f"分类 '{safe_name}' 已存在")
            return {'CANCELLED'}

        try:
            new_folder_path.mkdir(parents=True, exist_ok=False)
            self.report({'INFO'}, f"已创建分类 '{safe_name}'")
            return {'FINISHED'}
        except OSError as e:
            self.report({'ERROR'}, f"创建分类 '{safe_name}' 时出错 {e}")
            return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"创建分类时发生未知错误 {e}")
            return {'CANCELLED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

class ANIM_OT_DeleteLibraryFolder(bpy.types.Operator):

    bl_idname = "anim.delete_library_folder"
    bl_label = "删除分类"
    bl_options = {'REGISTER', 'UNDO'}

    folder_to_delete: bpy.props.EnumProperty(
        name="要删除的分类",
        description="选择要永久删除的分类及其所有内容",
        items=get_library_folders
    )

    @classmethod
    def poll(cls, context):
        folders = get_library_folders(cls, context)
        return folders and folders[0][0] != "_NO_FOLDERS_"

    def execute(self, context):
        target_folder_name = self.folder_to_delete
        if target_folder_name == "_NO_FOLDERS_":
            self.report({'ERROR'}, "没有选择有效的分类")
            return {'CANCELLED'}

        folder_path = LIBRARY_PATH / target_folder_name

        if not folder_path.is_dir():
            self.report({'ERROR'}, f"分类文件夹未找到 {folder_path}")
            return {'CANCELLED'}

        try:
            print(f"即将永久删除文件夹及其内容 {folder_path}")
            shutil.rmtree(str(folder_path))
            print(f"已删除文件夹 {folder_path}")

            props = context.scene.anim_merge_props
            indices_to_remove = []
            for i, item in enumerate(props.action_library_items):
                if item.source_fbx == target_folder_name:
                    indices_to_remove.append(i)

            removed_count = 0
            for i in sorted(indices_to_remove, reverse=True):
                try:
                    props.action_library_items.remove(i)
                    removed_count += 1
                except Exception as e:
                    print(f"从列表移除项目 {i} 时出错 {e}")

            if props.action_library_items:
                 current_index = props.action_library_index
                 if current_index in indices_to_remove:
                     props.action_library_index = 0
                 else:
                      removed_before_current = sum(1 for i in indices_to_remove if i < current_index)
                      props.action_library_index = max(0, current_index - removed_before_current)
            else:
                 props.action_library_index = -1

            self.report({'INFO'}, f"已删除分类 '{target_folder_name}' 并移除了 {removed_count} 个动作项")
            context.area.tag_redraw()
            return {'FINISHED'}

        except OSError as e:
            self.report({'ERROR'}, f"删除文件夹 '{target_folder_name}' 时出错 {e}")
            return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"删除分类时发生未知错误 {e}")
            return {'CANCELLED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "folder_to_delete")
        layout.separator()
        warning_box = layout.box()
        warning_box.alert = True
        warning_box.label(text="警告 此操作将永久删除所选分类及其包含的所有动作文件", icon='ERROR')
        warning_box.label(text="此操作无法撤销", icon='CANCEL_M')

class ANIM_OT_CreateBezierPath(bpy.types.Operator):
    bl_idname = "anim.create_bezier_path"
    bl_label = "创建贝塞尔路径"
    bl_description = "为选中物体创建一条贝塞尔路径曲线"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        # 修改判断条件，现在允许骨架(armature)类型的物体
        return context.active_object is not None
    
    def execute(self, context):
        props = context.scene.path_anim_props
        active_obj = context.active_object
        
        # 如果已存在曲线，先删除
        if props.curve_object and props.curve_object.name in bpy.data.objects:
            bpy.data.objects.remove(props.curve_object, do_unlink=True)
        
        # 创建贝塞尔曲线
        curve_data = bpy.data.curves.new(name=f"{active_obj.name}_Path", type='CURVE')
        curve_data.dimensions = '3D'
        curve_data.resolution_u = 12
        
        # 设置曲线显示属性 - 使用安全的方式设置控制点显示
        # 注意：不是所有Blender版本的Curve对象都有show_handles属性
        try:
            # 尝试设置显示控制柄（较新版本的Blender）
            if hasattr(curve_data, "show_handles"):
                curve_data.show_handles = True
        except:
            pass  # 如果不支持，就跳过
            
        # 创建曲线对象
        curve_obj = bpy.data.objects.new(f"{active_obj.name}_Path", curve_data)
        context.collection.objects.link(curve_obj)
        
        # 创建样条
        spline = curve_data.splines.new(type='BEZIER')
        
        # 设置控制点数量
        if props.control_points > 1:
            spline.bezier_points.add(props.control_points - 1)
        
        # 初始曲线形状 - 围绕物体创建一个简单路径
        obj_loc = active_obj.location
        obj_size = 2.0
        
        # 计算控制点位置和手柄
        points_count = len(spline.bezier_points)
        for i, point in enumerate(spline.bezier_points):
            # 计算控制点位置 (围绕物体创建一个圆形路径)
            angle = (i / points_count) * 2 * math.pi
            x = obj_loc.x + obj_size * math.cos(angle)
            y = obj_loc.y + obj_size * math.sin(angle)
            z = obj_loc.z
            
            # 设置控制点位置
            point.co = (x, y, z)
            
            # 计算手柄方向 (圆形的切线)
            handle_angle = angle + math.pi/2
            handle_x = 0.5 * math.cos(handle_angle)
            handle_y = 0.5 * math.sin(handle_angle)
            
            # 设置控制点手柄 (使曲线平滑)
            point.handle_left = (x - handle_x, y - handle_y, z)
            point.handle_right = (x + handle_x, y + handle_y, z)
            
            # 设置控制点手柄类型
            point.handle_left_type = 'ALIGNED'
            point.handle_right_type = 'ALIGNED'
        
        # 存储创建的曲线对象在属性中
        props.curve_object = curve_obj
        props.applied_control_points = props.control_points
        
        # 选择曲线对象以便编辑
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        curve_obj.select_set(True)
        context.view_layer.objects.active = curve_obj
        
        # 切换到编辑模式以便用户可以立即编辑控制点
        bpy.ops.object.mode_set(mode='EDIT')
        
        self.report({'INFO'}, f"已创建贝塞尔路径，请调整形状后点击应用")
        return {'FINISHED'}

# 修改应用路径动画操作符，使用Blender的曲线评估API获取精确位置和切线
class ANIM_OT_ApplyPathAnimation(bpy.types.Operator):
    bl_idname = "anim.apply_path_animation"
    bl_label = "应用路径动画"
    bl_description = "将选中物体沿贝塞尔路径移动 (精确采样)"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        props = context.scene.path_anim_props
        # 修改判断条件，现在允许骨架(armature)类型的物体
        return (props.curve_object and 
                props.curve_object.name in bpy.data.objects and 
                context.active_object)
    
    def execute(self, context):
        props = context.scene.path_anim_props
        active_obj = context.active_object
        curve_obj = props.curve_object
        
        # 保存当前帧
        orig_frame = context.scene.frame_current
        
        # 确保处于对象模式
        if active_obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        
        # 确保曲线对象也处于对象模式
        if curve_obj.mode != 'OBJECT':
            old_active = context.view_layer.objects.active
            context.view_layer.objects.active = curve_obj
            bpy.ops.object.mode_set(mode='OBJECT')
            context.view_layer.objects.active = old_active
        
        # 检查物体类型并打印相应消息
        is_armature = active_obj.type == 'ARMATURE'
        if is_armature:
            print(f"--- 开始为骨架 ({active_obj.name}) 创建精确路径动画 ---")
        else:
            print(f"--- 开始创建精确路径动画 ({active_obj.name}) ---")
        
        # 1. 基本参数设置
        start_frame = props.frame_start
        base_duration = props.frame_end - props.frame_start
        speed_factor = max(0.01, props.speed_factor)
        adjusted_duration = int(base_duration / speed_factor)
        end_frame = start_frame + adjusted_duration
        
        # 确保Curve启用了Path参数
        curve_obj.data.use_path = True
        # 设置曲线分辨率和路径时长
        curve_obj.data.resolution_u = max(32, curve_obj.data.resolution_u)  # 确保足够高的分辨率
        curve_obj.data.path_duration = base_duration
        
        # 创建动画数据
        if not active_obj.animation_data:
            active_obj.animation_data_create()
        
        # 创建或重用动作
        action_name = f"{active_obj.name}_PathAction"
        if action_name in bpy.data.actions:
            action = bpy.data.actions[action_name]
            # 清除旧的位置和旋转曲线
            fcurves_to_remove = []
            for fc in action.fcurves:
                if fc.data_path in ["location", "rotation_euler", "rotation_quaternion"]:
                    fcurves_to_remove.append(fc)
            for fc in fcurves_to_remove:
                action.fcurves.remove(fc)
        else:
            action = bpy.data.actions.new(action_name)
        
        active_obj.animation_data.action = action
        
        # 2. 为位置和旋转创建FCurves
        loc_fcurves = []
        for i in range(3):  # XYZ
            fc = action.fcurves.new(data_path="location", index=i)
            loc_fcurves.append(fc)
        
        rot_fcurves = []
        if props.follow_path:
            rotation_mode = active_obj.rotation_mode
            if rotation_mode == 'QUATERNION':
                for i in range(4):  # WXYZ
                    fc = action.fcurves.new(data_path="rotation_quaternion", index=i)
                    rot_fcurves.append(fc)
            else:  # 欧拉旋转
                for i in range(3):  # XYZ
                    fc = action.fcurves.new(data_path="rotation_euler", index=i)
                    rot_fcurves.append(fc)
        
        # 3. 准备曲线路径计算
        import mathutils
        curve_data = curve_obj.data
        splines = curve_data.splines
        matrix_world = curve_obj.matrix_world
        
        if not splines:
            self.report({'ERROR'}, f"曲线对象 {curve_obj.name} 没有样条曲线")
            return {'CANCELLED'}
        
        # 使用样条曲线的路径评估函数
        print(f"获取曲线路径采样数据...")
        
        # 需要的采样数量（每帧一个采样点）
        num_samples = adjusted_duration + 1  
        samples_per_segment = 10  # 每段曲线的额外采样点，用于平滑
        
        # 收集位置和方向数据
        curve_points = []  # 将存储 (位置, 方向) 元组
        total_length = 0  # 用于计算曲线总长度
        prev_point = None
        
        # 使用细分采样收集路径数据
        for spline in splines:
            # 获取样条曲线的点
            if spline.type == 'BEZIER':
                # 对贝塞尔曲线进行精确采样
                points = spline.bezier_points
                segments = len(points) - 1
                
                if segments <= 0:
                    continue  # 跳过单点曲线
                
                # 对每段曲线进行细分采样
                for i in range(segments):
                    p0 = points[i]
                    p1 = points[i+1]
                    
                    # 对这段曲线进行均匀采样
                    for t_sub in range(samples_per_segment + 1):
                        t = t_sub / samples_per_segment
                        # 计算贝塞尔曲线上的精确点
                        # p0.co, p0.handle_right, p1.handle_left, p1.co 是贝塞尔曲线段的4个控制点
                        
                        bezier_point, bezier_tangent = self.evaluate_bezier_curve(
                            matrix_world @ p0.co,
                            matrix_world @ p0.handle_right,
                            matrix_world @ p1.handle_left,
                            matrix_world @ p1.co,
                            t
                        )
                        
                        if prev_point is not None:
                            segment_length = (bezier_point - prev_point).length
                            total_length += segment_length
                        
                        curve_points.append((bezier_point, bezier_tangent.normalized()))
                        prev_point = bezier_point
                
        # 4. 根据长度参数重新采样以获取均匀的点
        if len(curve_points) < 2:
            self.report({'ERROR'}, "曲线太短，无法创建路径动画")
            return {'CANCELLED'}
            
        # 根据路径长度重采样，确保均匀分布，便于基于速度的控制
        resampled_points = []
        distances = [0]  # 起点距离为0
        
        # 计算累计距离
        for i in range(1, len(curve_points)):
            pos_prev, _ = curve_points[i-1]
            pos_curr, _ = curve_points[i]
            distances.append(distances[i-1] + (pos_curr - pos_prev).length)
            
        # 5. 应用插值类型和创建关键帧
        print(f"创建关键帧，应用插值类型: {props.interpolation_type}...")
        
        # 根据插值类型处理采样
        for i in range(num_samples):
            frame = start_frame + i
            
            # 根据插值类型计算不同的时间参数
            if props.interpolation_type == 'LINEAR':
                # 线性插值 - 均匀分布的 t
                t = i / (num_samples - 1) if num_samples > 1 else 0
            elif props.interpolation_type == 'EASE_IN':
                # 先慢后快 - 使用二次方插值
                t_raw = i / (num_samples - 1) if num_samples > 1 else 0
                t = t_raw * t_raw  # 二次方加速
            elif props.interpolation_type == 'EASE_OUT':
                # 先快后慢 - 使用二次方插值
                t_raw = i / (num_samples - 1) if num_samples > 1 else 0
                t = 1 - (1 - t_raw) * (1 - t_raw)  # 二次方减速
            else:
                # 默认线性
                t = i / (num_samples - 1) if num_samples > 1 else 0
            
            # 计算目标距离
            target_distance = t * distances[-1]
            
            # 二分查找找到最接近的点
            idx_low = 0
            idx_high = len(distances) - 1
            while idx_high - idx_low > 1:
                idx_mid = (idx_low + idx_high) // 2
                if distances[idx_mid] < target_distance:
                    idx_low = idx_mid
                else:
                    idx_high = idx_mid
                    
            # 在找到的段上插值
            segment_t = 0
            if idx_high > idx_low:
                segment_length = distances[idx_high] - distances[idx_low]
                if segment_length > 0:
                    segment_t = (target_distance - distances[idx_low]) / segment_length
            
            pos_low, dir_low = curve_points[idx_low]
            pos_high, dir_high = curve_points[idx_high]
            
            # 位置线性插值
            position = pos_low.lerp(pos_high, segment_t)
            
            # 方向球面插值 (对单位向量)
            direction = dir_low.slerp(dir_high, segment_t)
            if direction.length < 0.001:  # 避免零向量
                direction = dir_high if dir_high.length > 0.001 else mathutils.Vector((1, 0, 0))
                
            # 位置关键帧
            for axis in range(3):
                loc_fcurves[axis].keyframe_points.insert(frame, position[axis])
            
            # 旋转关键帧 (如果启用了旋转跟随)
            if props.follow_path and rot_fcurves:
                # 构建朝向矩阵 - 以direction为前方向
                forward = direction
                up = mathutils.Vector((0, 0, 1))  # 假设Z轴向上
                
                # 解决接近平行时的问题
                if abs(forward.dot(up)) > 0.99:
                    up = mathutils.Vector((0, 1, 0))  # 换用Y轴
                
                # 右手坐标系
                right = forward.cross(up).normalized()
                up = right.cross(forward).normalized()
                
                # 创建旋转矩阵
                rot_matrix = mathutils.Matrix((
                    right,
                    forward,
                    up
                ))
                rot_matrix.transpose()  # 矩阵转置
                
                # 根据物体的旋转模式设置
                if rotation_mode == 'QUATERNION':
                    quat = rot_matrix.to_quaternion()
                    for j in range(4):
                        rot_fcurves[j].keyframe_points.insert(frame, quat[j])
                else:
                    euler = rot_matrix.to_euler(rotation_mode)
                    for j in range(3):
                        rot_fcurves[j].keyframe_points.insert(frame, euler[j])
        
        # 6. 更新F曲线
        print("更新F曲线...")
        for fc in loc_fcurves + rot_fcurves:
            # 设置统一的线性插值，因为我们已经应用了自定义速度曲线
            for kp in fc.keyframe_points:
                kp.interpolation = 'LINEAR'
            try:
                fc.update()
            except Exception as e:
                print(f"更新FCurve时出错: {e}")
        
        # 7. 应用循环修改器
        if props.loop_type != 'NONE':
            print(f"应用循环模式: {props.loop_type}, 循环次数: {props.loop_count}, 间隔: {props.loop_gap}")
            
            # 记录有效的动画帧范围
            anim_start = start_frame
            anim_end = end_frame
            original_duration = anim_end - anim_start
            
            # 如果使用PING_PONG模式，需要手动创建返程路径
            if props.loop_type == 'PING_PONG':
                print("为PING_PONG模式创建返程路径...")
                
                # 计算返程开始帧和结束帧
                return_start_frame = anim_end
                return_end_frame = anim_start + (original_duration * 2)
                return_frames = return_end_frame - return_start_frame
                
                # 收集正向路径上的所有关键帧信息
                forward_keyframes = {}
                for fc_idx, fc in enumerate(loc_fcurves):
                    forward_keyframes[fc_idx] = []
                    for kp in fc.keyframe_points:
                        if anim_start <= kp.co.x <= anim_end:
                            forward_keyframes[fc_idx].append((kp.co.x, kp.co.y))
                
                # 同样收集旋转关键帧
                if props.follow_path and rot_fcurves:
                    for fc_idx, fc in enumerate(rot_fcurves):
                        forward_keyframes[fc_idx + len(loc_fcurves)] = []
                        for kp in fc.keyframe_points:
                            if anim_start <= kp.co.x <= anim_end:
                                forward_keyframes[fc_idx + len(loc_fcurves)].append((kp.co.x, kp.co.y))
                
                # 创建返程关键帧 - 反向添加所有正向路径的点
                # 注意帧号需要映射到返程区域
                for fc_idx, keyframes in forward_keyframes.items():
                    # 排序确保按帧顺序处理
                    sorted_keyframes = sorted(keyframes, key=lambda x: x[0])
                    
                    # 反向遍历排序后的帧
                    for i, (orig_frame, value) in enumerate(reversed(sorted_keyframes)):
                        # 跳过第一个点，避免在同一帧处添加两次关键帧
                        if i == 0 and orig_frame == anim_end:
                            continue
                            
                        # 计算对应的返程帧号
                        # 它应该是从返程起点开始的相对位置
                        rel_pos = (orig_frame - anim_start) / original_duration
                        return_frame = return_end_frame - int(rel_pos * return_frames)
                        
                        # 根据fc_idx确定是位置还是旋转曲线
                        if fc_idx < len(loc_fcurves):
                            # 位置曲线
                            loc_fcurves[fc_idx].keyframe_points.insert(return_frame, value)
                        else:
                            # 旋转曲线 - 需要考虑方向反转
                            rot_idx = fc_idx - len(loc_fcurves)
                            if rot_idx < len(rot_fcurves):
                                # 如果是旋转方向相关的分量，可能需要调整值
                                # 这里简单复用原值，可能需要根据实际情况调整
                                rot_fcurves[rot_idx].keyframe_points.insert(return_frame, value)
                
                # 确保返程末尾有与起点相同的关键帧
                for fc_idx, fc in enumerate(loc_fcurves):
                    # 查找起始帧的值
                    start_value = None
                    for kp in fc.keyframe_points:
                        if kp.co.x == anim_start:
                            start_value = kp.co.y
                            break
                            
                    if start_value is not None:
                        # 在返程结束处添加与起点相同的关键帧
                        fc.keyframe_points.insert(return_end_frame, start_value)
                
                # 对旋转也做同样处理
                if props.follow_path and rot_fcurves:
                    for fc_idx, fc in enumerate(rot_fcurves):
                        start_value = None
                        for kp in fc.keyframe_points:
                            if kp.co.x == anim_start:
                                start_value = kp.co.y
                                break
                                
                        if start_value is not None:
                            fc.keyframe_points.insert(return_end_frame, start_value)
                
                # 更新结束帧为返程结束帧
                end_frame = return_end_frame
                
                # 对所有关键帧应用线性插值，确保平滑过渡
                for fc in loc_fcurves + rot_fcurves:
                    for kp in fc.keyframe_points:
                        kp.interpolation = 'LINEAR'
                    try:
                        fc.update()
                    except Exception as e:
                        print(f"更新FCurve时出错: {e}")
            
            # 如果设置了循环间隔，需要调整关键帧
            if props.loop_gap > 0 and props.loop_type == 'REPEAT':
                # 需要在动画末尾添加间隔帧 - 保持末尾值不变
                gap_start = end_frame + 1
                gap_end = gap_start + props.loop_gap - 1
                
                # 为位置和旋转添加间隔帧
                for fc in loc_fcurves:
                    # 获取最后一帧的值
                    last_value = None
                    for kp in fc.keyframe_points:
                        if kp.co.x == end_frame:
                            last_value = kp.co.y
                            break
                    
                    if last_value is not None:
                        # 在间隔开始和结束处添加相同值的关键帧
                        fc.keyframe_points.insert(gap_start, last_value)
                        fc.keyframe_points.insert(gap_end, last_value)
                
                # 对旋转也做同样处理
                if props.follow_path:
                    for fc in rot_fcurves:
                        # 获取最后一帧的值
                        last_value = None
                        for kp in fc.keyframe_points:
                            if kp.co.x == end_frame:
                                last_value = kp.co.y
                                break
                        
                        if last_value is not None:
                            # 在间隔开始和结束处添加相同值的关键帧
                            fc.keyframe_points.insert(gap_start, last_value)
                            fc.keyframe_points.insert(gap_end, last_value)
                
                # 更新结束帧
                end_frame = gap_end
            
            # 应用循环修改器到所有F曲线
            # 对于PING_PONG模式，我们已经手动创建了一个完整的来回，只需要在循环次数>1时添加重复修改器
            for fc in loc_fcurves + rot_fcurves:
                # 移除现有循环修改器
                for mod in list(fc.modifiers):
                    if mod.type == 'CYCLES':
                        fc.modifiers.remove(mod)
                
                # 对于PING_PONG，我们已经手动创建了一个完整的来回，只需要在循环次数>1时添加重复修改器
                if props.loop_count > 1 or props.loop_count == 0:  # 大于1次或无限循环
                    mod = fc.modifiers.new('CYCLES')
                    
                    # 无论是什么循环类型，都使用REPEAT模式，因为我们已经手动处理了PING_PONG的来回
                    mod.mode_before = 'REPEAT'
                    mod.mode_after = 'REPEAT'
                    
                    # 设置循环次数 (0表示无限循环)
                    if props.loop_count > 1:
                        # 每个循环单位现在是一次完整的来回(对于PING_PONG)或一次往返(对于REPEAT)
                        cycle_length = end_frame - anim_start
                        
                        # 设置修改器的结束帧，使其在指定次数后停止
                        cycle_range = cycle_length * (props.loop_count - 1)
                        last_frame = anim_start + cycle_range
                        
                        # 这里使用repeat_end参数限制循环范围
                        mod.use_restricted_range = True
                        mod.frame_start = anim_start
                        mod.frame_end = last_frame
            
            # 更新场景结束帧以包含所有循环
            if props.loop_count > 0:
                # 计算单循环长度 - 对于PING_PONG已经包含来回
                cycle_length = end_frame - anim_start
                
                # 计算总帧数：如果循环次数为1，只包含原始动画长度(可能包括PING_PONG的返程)
                if props.loop_count == 1:
                    total_frames = end_frame
                else:
                    # 动画起点 + 一次完整循环 + 额外循环的长度
                    total_frames = anim_start + cycle_length * props.loop_count
                
                # 只有当用户选择了更新时间线选项时，才更新场景的结束帧
                if props.update_timeline:
                    context.scene.frame_end = max(end_frame, total_frames)
                    print(f"场景总帧数已更新: {context.scene.frame_end} (循环 {props.loop_count} 次)")
                else:
                    print(f"建议的场景总帧数: {max(end_frame, total_frames)} (未更新时间线)")
            else:
                # 无限循环，计算建议的时间线长度
                cycle_length = end_frame - anim_start
                suggested_end_frame = anim_start + cycle_length * 3  # 显示3个循环
                
                # 只有当用户选择了更新时间线选项时，才更新场景的结束帧
                if props.update_timeline:
                    context.scene.frame_end = suggested_end_frame
                    print(f"场景总帧数已更新: {context.scene.frame_end} (无限循环)")
                else:
                    print(f"建议的场景总帧数: {suggested_end_frame} (未更新时间线)")
        else:
            # 无循环
            # 只有当用户选择了更新时间线选项时，才更新场景的结束帧
            if props.update_timeline:
                context.scene.frame_end = end_frame
                print(f"场景总帧数已更新: {end_frame} (无循环)")
            else:
                print(f"建议的场景总帧数: {end_frame} (未更新时间线)")
        
        # 8. 设置场景帧范围和收尾
        if props.update_timeline:
            context.scene.frame_start = start_frame
        context.scene.frame_current = start_frame
        
        context.view_layer.objects.active = active_obj
        active_obj.select_set(True)
        
        # 更新视图
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        
        print("--- 路径动画创建完成 ---")
        if is_armature:
            self.report({'INFO'}, f"已为骨架 {active_obj.name} 创建沿贝塞尔路径的精确动画")
        else:
            self.report({'INFO'}, f"已为 {active_obj.name} 创建沿贝塞尔路径的精确动画")
        return {'FINISHED'}
    
    def evaluate_bezier_curve(self, p0, p1, p2, p3, t):
        """
        计算贝塞尔曲线上的点和切线
        p0, p1, p2, p3: 四个控制点
        t: 参数 (0-1)
        返回: (位置, 切线方向)
        """
        import mathutils
        
        # 点的计算
        mt = 1 - t
        mt2 = mt * mt
        mt3 = mt2 * mt
        t2 = t * t
        t3 = t2 * t
        
        # 贝塞尔公式计算坐标
        point = mt3 * p0 + 3 * mt2 * t * p1 + 3 * mt * t2 * p2 + t3 * p3
        
        # 切线的计算 (导数)
        tangent = 3 * mt2 * (p1 - p0) + 6 * mt * t * (p2 - p1) + 3 * t2 * (p3 - p2)
        
        return point, tangent

class ANIM_PT_PathAnimation(bpy.types.Panel):
    bl_label = "路径动画工具"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "动画工具"
    
    @classmethod
    def poll(cls, context):
        # 只在active_panel为PATH时显示面板
        return context.scene.anim_merge_props.active_panel == 'PATH'
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.path_anim_props
        
        # 获取活动对象，确保在每次绘制时刷新
        active_obj = context.active_object
        is_armature = active_obj and active_obj.type == 'ARMATURE'
        
        # 当前选中物体信息 - 现在允许骨架类型物体
        if active_obj:
            box = layout.box()
            if is_armature:
                box.label(text=f"当前骨架: {active_obj.name}", icon='ARMATURE_DATA')
            else:
                box.label(text=f"当前物体: {active_obj.name}", icon='OBJECT_DATA')
            
            # 创建贝塞尔路径按钮
            row = layout.row()
            row.operator("anim.create_bezier_path", icon='CURVE_BEZCURVE')
        else:
            box = layout.box()
            box.label(text="请先选择一个物体", icon='ERROR')
            row = layout.row()
            op = row.operator("anim.create_bezier_path", icon='CURVE_BEZCURVE')
            op.enabled = False
        
        # 当曲线已创建时显示设置
        if props.curve_object and props.curve_object.name in bpy.data.objects:
            box = layout.box()
            box.label(text="曲线设置", icon='SETTINGS')
            
            # 控制点数量和应用按钮
            row = box.row()
            row.label(text="控制点数量:")
            row.prop(props, "control_points", text="")
            
            # 应用控制点数量按钮（只有当设置有变化时才启用）
            row = box.row()
            if props.control_points != props.applied_control_points:
                row.operator("anim.apply_control_points", icon='CURVE_BEZCIRCLE')
            
            # 帧范围设置
            row = box.row()
            row.label(text="帧范围:")
            col = box.column(align=True)
            row = col.row(align=True)
            row.prop(props, "frame_start", text="开始")
            row.prop(props, "frame_end", text="结束")
            
            # 添加时间线更新选项
            row = box.row()
            row.prop(props, "update_timeline", text="更新场景时间线范围")
            
            # 速度设置
            row = box.row()
            row.label(text="速度:")
            row.prop(props, "speed_factor", text="")
            
            # 插值类型
            row = box.row()
            row.label(text="速度变化:")
            row.prop(props, "interpolation_type", text="")
            
            # 是否旋转
            row = box.row()
            row.prop(props, "follow_path")
            
            # 循环设置
            box_loop = box.box()
            row = box_loop.row()
            row.label(text="循环设置:", icon='LOOP_FORWARDS')
            row = box_loop.row()
            row.prop(props, "loop_type", text="")
            
            # 只有选择了循环类型才显示相关选项
            if props.loop_type != 'NONE':
                row = box_loop.row()
                row.prop(props, "loop_count", text="循环次数")
                
                if props.loop_type == 'REPEAT':
                    row = box_loop.row()
                    row.prop(props, "loop_gap", text="循环间隔")
            
            # 应用动画按钮
            apply_row = layout.row()
            apply_row.scale_y = 1.5
            apply_op = apply_row.operator("anim.apply_path_animation", text="应用路径动画", icon='PLAY')

class ANIM_OT_ApplyControlPoints(bpy.types.Operator):
    bl_idname = "anim.apply_control_points"
    bl_label = "应用控制点"
    bl_description = "应用新的控制点数量到当前曲线"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        props = context.scene.path_anim_props
        return (props.curve_object and 
                props.curve_object.name in bpy.data.objects and
                props.control_points != props.applied_control_points)
    
    def execute(self, context):
        props = context.scene.path_anim_props
        curve_obj = props.curve_object
        
        # 确保曲线对象有效
        if not curve_obj or curve_obj.name not in bpy.data.objects:
            self.report({'ERROR'}, "找不到曲线对象")
            return {'CANCELLED'}
        
        # 确保曲线对象是曲线类型
        if curve_obj.type != 'CURVE':
            self.report({'ERROR'}, "选择的对象不是曲线")
            return {'CANCELLED'}
        
        # 获取当前活动对象
        active_obj = context.active_object
        
        # 选择曲线对象
        for obj in context.selected_objects:
            obj.select_set(False)
        curve_obj.select_set(True)
        context.view_layer.objects.active = curve_obj
        
        # 确保处于对象模式
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        
        # 获取曲线数据和第一条样条线
        curve_data = curve_obj.data
        if len(curve_data.splines) == 0:
            self.report({'ERROR'}, "曲线没有样条线")
            return {'CANCELLED'}
        
        spline = curve_data.splines[0]
        if spline.type != 'BEZIER':
            self.report({'ERROR'}, "只支持贝塞尔样条线")
            return {'CANCELLED'}
        
        current_points = len(spline.bezier_points)
        target_points = props.control_points
        
        # 记录当前点的位置和手柄
        current_data = []
        for i, point in enumerate(spline.bezier_points):
            current_data.append({
                'co': tuple(point.co),
                'handle_left': tuple(point.handle_left),
                'handle_right': tuple(point.handle_right),
                'handle_left_type': point.handle_left_type,
                'handle_right_type': point.handle_right_type
            })
        
        # 根据是增加还是减少点进行操作
        if target_points > current_points:
            # 增加点
            spline.bezier_points.add(target_points - current_points)
            
            # 重新分配点的位置
            for i in range(target_points):
                # 使用线性插值计算新点的位置
                old_index = (i * (current_points - 1)) / (target_points - 1) if target_points > 1 else 0
                
                idx1 = int(old_index)
                idx2 = min(idx1 + 1, current_points - 1)
                frac = old_index - idx1
                
                # 使用之前保存的数据进行线性插值
                if idx1 == idx2 or frac < 0.001:
                    # 如果是相同的点或者非常接近，直接使用第一个点的数据
                    spline.bezier_points[i].co = current_data[idx1]['co']
                    spline.bezier_points[i].handle_left = current_data[idx1]['handle_left']
                    spline.bezier_points[i].handle_right = current_data[idx1]['handle_right']
                    spline.bezier_points[i].handle_left_type = current_data[idx1]['handle_left_type']
                    spline.bezier_points[i].handle_right_type = current_data[idx1]['handle_right_type']
                else:
                    # 否则进行线性插值
                    # 位置的线性插值
                    co1 = mathutils.Vector(current_data[idx1]['co'])
                    co2 = mathutils.Vector(current_data[idx2]['co'])
                    spline.bezier_points[i].co = co1.lerp(co2, frac)
                    
                    # 手柄的线性插值
                    left1 = mathutils.Vector(current_data[idx1]['handle_left'])
                    left2 = mathutils.Vector(current_data[idx2]['handle_left'])
                    spline.bezier_points[i].handle_left = left1.lerp(left2, frac)
                    
                    right1 = mathutils.Vector(current_data[idx1]['handle_right'])
                    right2 = mathutils.Vector(current_data[idx2]['handle_right'])
                    spline.bezier_points[i].handle_right = right1.lerp(right2, frac)
                    
                    # 设置手柄类型为FREE，允许更灵活的编辑
                    spline.bezier_points[i].handle_left_type = 'FREE'
                    spline.bezier_points[i].handle_right_type = 'FREE'
        
        elif target_points < current_points:
            # 减少点 - 需要先创建一个新的样条替换旧样条
            # 获取重采样后的位置
            resampled_data = []
            for i in range(target_points):
                old_index = (i * (current_points - 1)) / (target_points - 1) if target_points > 1 else 0
                idx1 = int(old_index)
                idx2 = min(idx1 + 1, current_points - 1)
                frac = old_index - idx1
                
                if idx1 == idx2 or frac < 0.001:
                    resampled_data.append(current_data[idx1])
                else:
                    # 进行线性插值
                    co1 = mathutils.Vector(current_data[idx1]['co'])
                    co2 = mathutils.Vector(current_data[idx2]['co'])
                    
                    left1 = mathutils.Vector(current_data[idx1]['handle_left'])
                    left2 = mathutils.Vector(current_data[idx2]['handle_left'])
                    
                    right1 = mathutils.Vector(current_data[idx1]['handle_right'])
                    right2 = mathutils.Vector(current_data[idx2]['handle_right'])
                    
                    resampled_data.append({
                        'co': co1.lerp(co2, frac),
                        'handle_left': left1.lerp(left2, frac),
                        'handle_right': right1.lerp(right2, frac),
                        'handle_left_type': 'FREE',
                        'handle_right_type': 'FREE'
                    })
            
            # 删除旧样条并创建新样条
            curve_data.splines.remove(spline)
            new_spline = curve_data.splines.new(type='BEZIER')
            
            # 添加足够数量的点
            if target_points > 1:
                new_spline.bezier_points.add(target_points - 1)
            
            # 设置新样条上的点位置和手柄
            for i, point_data in enumerate(resampled_data):
                new_spline.bezier_points[i].co = point_data['co']
                new_spline.bezier_points[i].handle_left = point_data['handle_left']
                new_spline.bezier_points[i].handle_right = point_data['handle_right']
                new_spline.bezier_points[i].handle_left_type = point_data['handle_left_type']
                new_spline.bezier_points[i].handle_right_type = point_data['handle_right_type']
        
        # 更新已应用的控制点数量
        props.applied_control_points = props.control_points
        
        # 进入编辑模式
        bpy.ops.object.mode_set(mode='EDIT')
        
        return {'FINISHED'}

# 在PathAnimationProperties类下方添加新的属性组
class BreathAnimationProperties(bpy.types.PropertyGroup):
    """骨骼呼吸动画设置"""
    
    # 动画帧范围
    frame_start: bpy.props.IntProperty(
        name="起始帧",
        description="呼吸动画的起始帧",
        default=1,
        min=1
    )
    
    frame_end: bpy.props.IntProperty(
        name="结束帧",
        description="呼吸动画的结束帧",
        default=100,
        min=2
    )
    
    # 旋转幅度 (角度)
    rotation_amount: bpy.props.FloatProperty(
        name="旋转幅度",
        description="骨骼旋转的最大角度(度)",
        default=3.0,
        min=0.1,
        max=20.0
    )
    
    # 旋转轴
    rotation_axis: bpy.props.EnumProperty(
        name="旋转轴",
        description="骨骼旋转的主要轴向",
        items=[
            ('X', "X轴", "沿X轴旋转"),
            ('Y', "Y轴", "沿Y轴旋转"),
            ('Z', "Z轴", "沿Z轴旋转")
        ],
        default='Z'
    )
    
    # 动画周期数
    cycles: bpy.props.IntProperty(
        name="周期数",
        description="在指定帧范围内完成的呼吸周期数",
        default=2,
        min=1,
        max=20
    )
    
    # 缓动类型
    easing_type: bpy.props.EnumProperty(
        name="缓动类型",
        description="呼吸动画的缓动方式",
        items=[
            ('SINE', "正弦", "平滑的正弦波动"),
            ('QUAD', "二次方", "更加缓慢的起止"),
            ('LINEAR', "线性", "均匀速度")
        ],
        default='SINE'
    )

# 添加一个呼吸动画操作符
class ANIM_OT_ApplyBreathAnimation(bpy.types.Operator):
    bl_idname = "anim.apply_breath_animation"
    bl_label = "应用呼吸动画"
    bl_description = "为选中的骨骼添加轻微旋转的呼吸效果"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        # 检查是否有活动的骨架且处于姿态模式
        return (context.active_object and 
                context.active_object.type == 'ARMATURE' and
                context.active_object.mode == 'POSE' and
                context.selected_pose_bones)
    
    def execute(self, context):
        import math
        import mathutils # 需要导入 mathutils

        props = context.scene.breath_anim_props
        obj = context.active_object # 使用 obj 而不是 armature
        scene = context.scene # 需要 scene 对象

        # Check if the active object is an armature
        if not obj or obj.type != 'ARMATURE':
            self.report({'WARNING'}, "请选择一个骨架对象")
            return {'CANCELLED'}

        # Check if in Pose Mode
        if context.mode != 'POSE':
            self.report({'WARNING'}, "请进入姿态模式 (Pose Mode)")
            return {'CANCELLED'}

        selected_pose_bones = context.selected_pose_bones
        if not selected_pose_bones:
            self.report({'WARNING'}, "请在姿态模式下选择至少一个骨骼") # 更清晰的提示
            return {'CANCELLED'}

        # Ensure animation data exists
        if not obj.animation_data:
            obj.animation_data_create()
        action = obj.animation_data.action
        if not action:
            # 使用 obj.name
            action = bpy.data.actions.new(name=f"{obj.name}_BreathAction")
            obj.animation_data.action = action

        # Animation parameters
        frame_start = props.frame_start
        frame_end = props.frame_end
        duration = frame_end - frame_start + 1
        if duration <= 0:
            self.report({'WARNING'}, "结束帧必须大于起始帧")
            return {'CANCELLED'}

        rotation_amount_rad = math.radians(props.rotation_amount)
        cycles = props.cycles
        # frequency = (2 * math.pi * cycles) / duration # Frequency not directly used
        axis = props.rotation_axis # 'X', 'Y', 'Z'
        easing_type = props.easing_type # 'LINEAR', 'EASE_IN', 'EASE_OUT', 'SINE', 'QUAD'

        # --- Main Loop ---\
        current_frame_scene = scene.frame_current # Store current frame

        try: # Use try/finally to restore frame
            for pb in selected_pose_bones:
                # --- Determine rotation mode and property ---
                rotation_prop = 'rotation_quaternion' if pb.rotation_mode == 'QUATERNION' else 'rotation_euler'
                is_quaternion = (rotation_prop == 'rotation_quaternion')

                # --- Get Base Rotation (evaluated at frame before start) ---
                eval_frame = max(scene.frame_start, frame_start - 1)
                if frame_start <= scene.frame_start: # Handle edge case
                     eval_frame = scene.frame_start

                # Store current pose bone state before changing frame (important!)
                original_quat = pb.rotation_quaternion.copy()
                original_euler = pb.rotation_euler.copy()
                original_matrix = pb.matrix.copy() # Store full matrix as well

                # Set frame and update dependency graph to get evaluated state
                scene.frame_set(eval_frame)
                obj.data.update_tag() # Tag armature data for update
                context.view_layer.update() # Evaluate the dependency graph

                # Read the *evaluated* rotation at eval_frame
                if is_quaternion:
                    base_rotation = pb.rotation_quaternion.copy()
                else:
                    # Ensure Euler mode is consistent if not Quaternion
                    if pb.rotation_mode not in {'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'}:
                         pb.rotation_mode = 'XYZ' # Default to XYZ Euler if not set
                         self.report({'INFO'}, f"骨骼 {pb.name} 旋转模式已设为 XYZ")
                    base_rotation = pb.rotation_euler.copy()

                # Restore original pose bone state immediately after reading base rotation
                pb.rotation_quaternion = original_quat
                pb.rotation_euler = original_euler
                pb.matrix = original_matrix # Restore matrix too
                context.view_layer.update() # Update view layer after restoring state


                # --- Apply Breath Animation Keyframes within the specified range ---
                for frame in range(frame_start, frame_end + 1):
                    # Calculate progress (0 to 1) within the duration
                    denom = duration - 1 if duration > 1 else 1
                    progress = (frame - frame_start) / denom

                    # Apply easing function to progress
                    eased_progress = progress # Default to linear
                    if easing_type == 'EASE_IN':
                        eased_progress = progress * progress # Quadratic ease-in
                    elif easing_type == 'EASE_OUT':
                        eased_progress = 1 - (1 - progress) * (1 - progress) # Quadratic ease-out
                    elif easing_type == 'SINE': # Keep existing easing types if desired
                        eased_progress = 0.5 * (1 - math.cos(progress * math.pi))
                    elif easing_type == 'QUAD': # Approximation? Better sine is usually used
                         # The previous QUAD implementation seemed complex, using simple Sine below
                         pass # Falls back to Sine via Sine calculation below

                    # Calculate the angle offset using a sine wave based on eased progress
                    # Ensures 'cycles' complete waves over the duration
                    # Common approach: angle_offset = amplitude * sin(phase)
                    # Phase goes from 0 to cycles * 2 * pi
                    phase = eased_progress * cycles * 2 * math.pi
                    angle_offset = rotation_amount_rad * math.sin(phase)


                    # Create the incremental rotation offset based on the axis
                    if is_quaternion:
                        # Create quaternion offset around the specified axis
                        axis_vector = mathutils.Vector((1.0 if axis == 'X' else 0.0,
                                                        1.0 if axis == 'Y' else 0.0,
                                                        1.0 if axis == 'Z' else 0.0))
                        breath_offset_rotation = mathutils.Quaternion(axis_vector, angle_offset)
                        # Combine base rotation with the offset
                        # Apply breath offset in the *local* space relative to the base pose
                        final_rotation = base_rotation @ breath_offset_rotation
                        pb.rotation_quaternion = final_rotation
                    else: # Euler
                        # Create Euler offset (applied to the specific axis)
                        # Combining Euler rotations directly can be tricky due to order.
                        # It's often safer to convert to matrices/quaternions for combination.
                        breath_offset_euler = mathutils.Euler((angle_offset if axis == 'X' else 0.0,
                                                               angle_offset if axis == 'Y' else 0.0,
                                                               angle_offset if axis == 'Z' else 0.0), pb.rotation_mode)

                        # Combine: Convert base Euler to matrix, multiply by offset Euler's matrix, convert back
                        base_matrix = base_rotation.to_matrix().to_4x4()
                        offset_matrix = breath_offset_euler.to_matrix().to_4x4()

                        # Apply breath offset locally relative to the base pose
                        final_matrix = base_matrix @ offset_matrix
                        # Convert back to Euler, using the bone's original mode
                        final_rotation = final_matrix.to_euler(pb.rotation_mode) # base_rotation is not needed here

                        pb.rotation_euler = final_rotation

                    # Insert keyframe for the calculated rotation at the current frame
                    # This will overwrite existing keyframes *only* for this property at this frame
                    pb.keyframe_insert(data_path=rotation_prop, frame=frame)

            self.report({'INFO'}, f"已为 {len(selected_pose_bones)} 个骨骼应用呼吸动画 [{frame_start}-{frame_end}]")

        except Exception as e:
            import traceback
            traceback.print_exc() # Print detailed error to console
            self.report({'ERROR'}, f"应用呼吸动画时出错: {e}")
            # Restore frame even on error
            scene.frame_set(current_frame_scene)
            # Update viewport
            context.view_layer.update()
            return {'CANCELLED'}
        finally:
            # Restore the original frame that was active before the operator ran
            scene.frame_set(current_frame_scene)
            # Update viewport to show final state/reflect changes
            context.view_layer.update()
            # Optional: Refresh editors if needed, but update() often suffices
            # for area in context.screen.areas:
            #     if area.type in ['VIEW_3D', 'DOPESHEET_EDITOR', 'GRAPH_EDITOR']:
            #         area.tag_redraw()

        return {'FINISHED'}

# 添加呼吸动画面板
class ANIM_PT_BreathAnimation(bpy.types.Panel):
    bl_label = "骨骼呼吸动画"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "动画工具"
    
    @classmethod
    def poll(cls, context):
        # 只在active_panel为BREATH时显示，但内容会根据是否有选中骨架和姿态模式而变化
        return context.scene.anim_merge_props.active_panel == 'BREATH'
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.breath_anim_props
        
        # 获取活动对象
        armature = context.active_object
        
        # 显示说明
        if armature.mode != 'POSE':
            box = layout.box()
            box.label(text="需要处于姿态模式", icon='INFO')
            row = layout.row()
            row.operator("object.mode_set", text="进入姿态模式").mode = 'POSE'
            return
        
        # 选中骨骼信息
        selected_bones = context.selected_pose_bones
        box = layout.box()
        if selected_bones:
            box.label(text=f"已选择 {len(selected_bones)} 个骨骼", icon='BONE_DATA')
        else:
            box.label(text="请选择需要添加呼吸动画的骨骼", icon='ERROR')
        
        # 参数设置
        box = layout.box()
        box.label(text="动画设置", icon='SETTINGS')
        
        # 帧范围设置
        row = box.row()
        row.label(text="帧范围:")
        col = box.column(align=True)
        row = col.row(align=True)
        row.prop(props, "frame_start", text="开始")
        row.prop(props, "frame_end", text="结束")
        
        # 旋转设置
        row = box.row()
        row.label(text="旋转设置:")
        col = box.column(align=True)
        row = col.row(align=True)
        row.prop(props, "rotation_amount")
        row = col.row(align=True)
        row.prop(props, "rotation_axis", expand=True)
        
        # 周期设置
        row = box.row()
        row.label(text="周期数:")
        row.prop(props, "cycles", text="")
        
        # 缓动类型
        row = box.row()
        row.label(text="缓动类型:")
        row.prop(props, "easing_type", text="")
        
        # 应用按钮 - 使用原生的启用/禁用方式
        is_ready = armature.mode == 'POSE' and bool(selected_bones)
        row = layout.row()
        row.scale_y = 1.5
        row.enabled = is_ready  # 设置行的启用状态，而不是operator的属性
        row.operator("anim.apply_breath_animation", icon='MOD_WAVE')
        
        # 提示信息
        col = layout.column()
        col.label(text="提示: 选择骨骼后点击应用按钮", icon='INFO')
        col.label(text="呼吸效果会创建轻微旋转动画")

class CameraAnimationProperties(bpy.types.PropertyGroup):
    """摄像机动画设置"""

    # --- 摄像机抖动设置 ---
    shake_frame_start: bpy.props.IntProperty(
        name="抖动起始帧",
        description="摄像机抖动效果的起始帧",
        default=1,
        min=1
    )
    shake_frame_end: bpy.props.IntProperty(
        name="抖动结束帧",
        description="摄像机抖动效果的结束帧",
        default=100,
        min=2
    )
    shake_type: bpy.props.EnumProperty(
        name="抖动类型",
        description="选择抖动的方式",
        items=[
            ('UP_DOWN', "上下摆动", "摄像机沿垂直方向(Z轴)摆动"),
            ('LEFT_RIGHT', "左右摆动", "摄像机沿水平方向(X轴)摆动"),
            ('RANDOM', "随机摆动", "摄像机在多个轴向上随机摆动")
        ],
        default='RANDOM'
    )
    shake_amount_pos: bpy.props.FloatProperty(
        name="位置抖动幅度",
        description="位置抖动的最大偏移量",
        default=0.1,
        min=0.0,
        max=5.0,
        unit='LENGTH'
    )
    shake_amount_rot: bpy.props.FloatProperty(
        name="旋转抖动幅度",
        description="旋转抖动的最大角度(度)",
        default=1.0,
        min=0.0,
        max=45.0,
        subtype='ANGLE',
        unit='ROTATION'
    )
    shake_frequency: bpy.props.FloatProperty(
        name="抖动频率",
        description="抖动的速度/频率 (值越高抖动越快)",
        default=5.0,
        min=0.1,
        max=50.0
    )
    shake_use_position: bpy.props.BoolProperty(
        name="启用位置抖动",
        description="是否应用位置上的抖动",
        default=True
    )
    shake_use_rotation: bpy.props.BoolProperty(
        name="启用旋转抖动",
        description="是否应用旋转上的抖动",
        default=True
    )

    # --- 摄像机跟踪设置 ---
    tracking_frame_start: bpy.props.IntProperty(
        name="跟踪起始帧",
        description="摄像机跟踪效果的起始帧",
        default=1,
        min=1
    )
    tracking_frame_end: bpy.props.IntProperty(
        name="跟踪结束帧",
        description="摄像机跟踪效果的结束帧",
        default=100,
        min=2
    )
    tracking_target: bpy.props.PointerProperty(
        type=bpy.types.Object,
        name="跟踪目标",
        description="选择摄像机要跟踪的物体或骨骼"
        # poll function can be added later to filter object types if needed
    )
    tracking_bone_target: bpy.props.StringProperty(
        name="跟踪骨骼名称",
        description="如果跟踪目标是骨架，指定要跟踪的具体骨骼名称"
    )
    tracking_mode: bpy.props.EnumProperty(
        name="跟踪模式",
        description="选择摄像机的跟踪方式",
        items=[
            ('ROTATION_ONLY', "仅旋转跟踪", "摄像机位置不变，仅旋转朝向目标"),
            ('POSITION_ONLY', "仅位置跟踪", "摄像机旋转不变，仅移动位置以保持相对朝向"),
            ('ORBIT_AND_TRACK', "环绕跟踪", "环绕目标旋转并跟踪目标")
        ],
        default='ROTATION_ONLY'
    )

    # 添加环绕旋转参数
    orbit_speed: bpy.props.FloatProperty(
        name="旋转速度",
        description="环绕目标旋转的速度 (度/帧)",
        default=1.0,
        min=0.1,
        max=10.0
    )

    orbit_revolutions: bpy.props.FloatProperty(
        name="旋转圈数",
        description="在整个帧范围内完成的旋转圈数",
        default=1.0,
        min=0.1,
        max=10.0
    )

    orbit_radius: bpy.props.FloatProperty(
        name="环绕半径",
        description="围绕目标旋转的距离",
        default=5.0,
        min=0.1,
        max=50.0,
        unit='LENGTH'
    )

    # 环绕跟踪的垂直角度
    orbit_vertical_angle: bpy.props.FloatProperty(
        name="俯仰角度",
        description="环绕时的俯视角或仰视角（正值为俯视，负值为仰视）",
        default=0.0,
        min=-89.0,
        max=89.0,
        subtype='ANGLE',
        unit='ROTATION'
    )

    # 环绕距离（如果已有orbit_radius则不需要重复添加）
    orbit_radius: bpy.props.FloatProperty(
        name="环绕距离",
        description="摄像机与目标的距离",
        default=5.0,
        min=0.1,
        max=50.0,
        unit='LENGTH'
    )

# 新增摄像机抖动操作符
class ANIM_OT_ApplyCameraShake(bpy.types.Operator):
    bl_idname = "anim.apply_camera_shake"
    bl_label = "应用摄像机抖动"
    bl_description = "为选中的摄像机在指定帧范围内添加抖动效果"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # 必须选中一个摄像机对象
        obj = context.active_object
        return obj is not None and obj.type == 'CAMERA'

    def execute(self, context):
        cam_obj = context.active_object
        scene = context.scene
        props = scene.camera_anim_props

        start_frame = props.shake_frame_start
        end_frame = props.shake_frame_end
        shake_type = props.shake_type
        amount_pos = props.shake_amount_pos
        amount_rot_deg = props.shake_amount_rot # 角度值
        frequency = props.shake_frequency
        use_pos = props.shake_use_position
        use_rot = props.shake_use_rotation

        if not use_pos and not use_rot:
            self.report({'WARNING'}, "未启用位置或旋转抖动")
            return {'CANCELLED'}

        if start_frame >= end_frame:
            self.report({'ERROR'}, "起始帧必须小于结束帧")
            return {'CANCELLED'}

        # --- 修改开始 ---
        # 清除现有范围内的抖动通道关键帧 (可选，防止重复应用)
        # 注意：这只会清除之前由这个工具添加的抖动相关F曲线修改器或关键帧
        # 如果想完全叠加，可以注释掉或细化清除逻辑
        # if cam_obj.animation_data and cam_obj.animation_data.action:
        #     action = cam_obj.animation_data.action
        #     for fcurve in action.fcurves:
        #         if "Shake" in [m.name for m in fcurve.modifiers if m.type == 'NOISE']:
        #              # 移除特定修改器或相关关键帧的逻辑会比较复杂
        #              # 简单起见，我们先假设用户知道自己在叠加
        #              pass # 暂时不清除

        # 确保对象有动画数据
        if not cam_obj.animation_data:
            cam_obj.animation_data_create()
        if not cam_obj.animation_data.action:
            # 创建新动作或使用现有动作
            action_name = f"{cam_obj.name}Action"
            cam_obj.animation_data.action = bpy.data.actions.get(action_name) or bpy.data.actions.new(name=action_name)

        action = cam_obj.animation_data.action
        current_frame_original = scene.frame_current # 保存当前帧

        # 准备随机种子，确保每次运行结果不同但单次运行内一致
        random.seed(time.time())
        # 为每个轴向生成不同的随机偏移，使噪声看起来更自然
        seed_offset_x = random.random() * 100
        seed_offset_y = random.random() * 100
        seed_offset_z = random.random() * 100
        seed_offset_rx = random.random() * 100
        seed_offset_ry = random.random() * 100
        seed_offset_rz = random.random() * 100


        # 迭代每一帧应用混合抖动
        for frame in range(start_frame, end_frame + 1):
            scene.frame_set(frame) # 设置场景到当前帧以获取正确的变换

            # 1. 获取当前帧的原始世界变换
            original_matrix = cam_obj.matrix_world.copy()
            orig_loc, orig_rot_quat, orig_scale = original_matrix.decompose()

            # --- 计算抖动偏移量 ---
            pos_offset = mathutils.Vector((0.0, 0.0, 0.0))
            rot_offset_euler = mathutils.Euler((0.0, 0.0, 0.0), 'XYZ') # 使用XYZ欧拉角

            time_param = frame / frequency # 时间参数影响噪声变化

            if use_pos:
                noise_x = (random.random() * 2 - 1) if shake_type == 'RANDOM' else 0
                noise_y = (random.random() * 2 - 1) if shake_type == 'RANDOM' else ((random.random() * 2 - 1) if shake_type == 'LEFT_RIGHT' else 0)
                noise_z = (random.random() * 2 - 1) if shake_type == 'RANDOM' else ((random.random() * 2 - 1) if shake_type == 'UP_DOWN' else 0)

                # 使用简单的随机数模拟 (也可以换成更平滑的噪声函数如 mathutils.noise)
                # 这里用 random 模拟快速抖动
                pos_offset.x = noise_x * amount_pos
                pos_offset.y = noise_y * amount_pos
                pos_offset.z = noise_z * amount_pos
                # # 如果需要更平滑的噪声:
                # pos_offset.x = (mathutils.noise.noise(mathutils.Vector((time_param + seed_offset_x, 0, 0))) * 2 -1) * amount_pos if shake_type in ['RANDOM','LEFT_RIGHT'] else 0
                # pos_offset.y = (mathutils.noise.noise(mathutils.Vector((0, time_param + seed_offset_y, 0))) * 2 -1) * amount_pos if shake_type == 'RANDOM' else 0
                # pos_offset.z = (mathutils.noise.noise(mathutils.Vector((0, 0, time_param + seed_offset_z))) * 2 -1) * amount_pos if shake_type in ['RANDOM','UP_DOWN'] else 0


            if use_rot:
                amount_rot_rad = math.radians(amount_rot_deg) # 转换为弧度
                noise_rx = (random.random() * 2 - 1) if shake_type == 'RANDOM' else 0
                noise_ry = (random.random() * 2 - 1) if shake_type == 'RANDOM' else ((random.random() * 2 - 1) if shake_type == 'LEFT_RIGHT' else 0) # 假设左右摆动影响Y轴旋转
                noise_rz = (random.random() * 2 - 1) if shake_type == 'RANDOM' else ((random.random() * 2 - 1) if shake_type == 'UP_DOWN' else 0) # 假设上下摆动影响Z轴旋转


                rot_offset_euler.x = noise_rx * amount_rot_rad
                rot_offset_euler.y = noise_ry * amount_rot_rad
                rot_offset_euler.z = noise_rz * amount_rot_rad
                # rot_offset_euler.x = (mathutils.noise.noise(mathutils.Vector((time_param + seed_offset_rx, 10, 10))) * 2 -1) * amount_rot_rad if shake_type == 'RANDOM' else 0
                # rot_offset_euler.y = (mathutils.noise.noise(mathutils.Vector((20, time_param + seed_offset_ry, 20))) * 2 -1) * amount_rot_rad if shake_type in ['RANDOM','LEFT_RIGHT'] else 0
                # rot_offset_euler.z = (mathutils.noise.noise(mathutils.Vector((30, 30, time_param + seed_offset_rz))) * 2 -1) * amount_rot_rad if shake_type in ['RANDOM','UP_DOWN'] else 0


            new_loc = orig_loc + pos_offset
            rot_offset_quat = rot_offset_euler.to_quaternion()
            new_rot_quat = orig_rot_quat @ rot_offset_quat
            # 应用混合后的变换到对象 (临时，为了插帧) 
            # 设置局部变换 即内部坐标系
            # 如果父级有变换，需要转换回内部坐标系
            if cam_obj.parent:
                 parent_inv = cam_obj.matrix_parent_inverse.copy()
                 new_matrix_world = mathutils.Matrix.Translation(new_loc) @ new_rot_quat.to_matrix().to_4x4() @ mathutils.Matrix.Diagonal(orig_scale).to_4x4()
                 new_matrix_local = parent_inv @ new_matrix_world
                 cam_obj.matrix_local = new_matrix_local
            else:
                 cam_obj.location = new_loc
                 cam_obj.rotation_quaternion = new_rot_quat
                 # cam_obj.rotation_euler = new_rot_quat.to_euler('XYZ') 
                 cam_obj.scale = orig_scale # 缩放不变


            # 插入
            if use_pos:
                cam_obj.keyframe_insert(data_path="location", frame=frame)
            if use_rot:
                cam_obj.keyframe_insert(data_path="rotation_quaternion", frame=frame)
                # cam_obj.keyframe_insert(data_path="rotation_euler", frame=frame)

        scene.frame_set(current_frame_original)


        self.report({'INFO'}, f"已在帧 {start_frame}-{end_frame} 应用摄像机抖动")
        return {'FINISHED'}

# 新增摄像机跟踪操作符
class ANIM_OT_ApplyCameraTracking(bpy.types.Operator):
    bl_idname = "anim.apply_camera_tracking"
    bl_label = "应用摄像机跟踪"
    bl_description = "使选中的摄像机在指定帧范围内跟踪目标"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.camera_anim_props
        return (context.active_object and 
                context.active_object.type == 'CAMERA' and 
                props.tracking_target)
    def execute(self, context):
        scene = context.scene
        props = scene.camera_anim_props
        cam = context.active_object
        target = props.tracking_target

        # Add imports if they are not already at the top of the file
        import math
        import mathutils
        import traceback # For error reporting

        if not target:
            self.report({'WARNING'}, "请先选择一个跟踪目标")
            return {'CANCELLED'}

        # 获取参数
        start_frame = props.tracking_frame_start
        end_frame = props.tracking_frame_end
        tracking_mode = props.tracking_mode

        if end_frame <= start_frame:
            self.report({'WARNING'}, "结束帧必须大于起始帧")
            return {'CANCELLED'}

        # --- 准备动画数据和 F-Curves ---
        if not cam.animation_data:
            cam.animation_data_create()
        action = cam.animation_data.action
        if not action:
            # Use a more specific name for the action
            action = bpy.data.actions.new(f"{cam.name}_TrackingAction")
            cam.animation_data.action = action

        # --- 确定旋转模式和数据路径 ---
        # Prefer Quaternion, fall back to Euler XYZ for simplicity in calculations hi i'm deepseekR1 
        rot_mode = cam.rotation_mode
        if rot_mode not in {'QUATERNION', 'AXIS_ANGLE'}:
            # Force Euler XYZ if it's an Euler mode but not simple XYZ
            if rot_mode not in ('XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'):
                cam.rotation_mode = 'XYZ' # Set the object's mode
            rot_mode = cam.rotation_mode # Read the potentially changed mode
            rot_data_path = 'rotation_euler'
            rot_indices = range(3) # X, Y, Z
        elif rot_mode == 'QUATERNION':
            rot_data_path = 'rotation_quaternion'
            rot_indices = range(4) # W, X, Y, Z
        else: # AXIS_ANGLE is tricky to handle directly with tracking math
             self.report({'WARNING'}, f"暂不支持 {rot_mode} 旋转模式的直接跟踪，请切换到欧拉或四元数")
             return {'CANCELLED'}

        loc_data_path = 'location'

        # --- 辅助函数: 获取/创建 F-Curve 并清除范围 ---
        def get_or_create_fcurve_and_clear_range(data_path, index):
            fcurve = action.fcurves.find(data_path, index=index)
            if not fcurve:
                # This is where the original error occurred if the fcurve existed
                # but bpy.ops tried to create it again. `find` avoids this.
                fcurve = action.fcurves.new(data_path, index=index)

            # Clear keyframes *only* within the target range before adding new ones
            keyframes_to_remove = [kp for kp in fcurve.keyframe_points if start_frame <= kp.co.x <= end_frame]
            # Iterate over a copy for safe removal
            for kp in keyframes_to_remove:
                 try:
                    fcurve.keyframe_points.remove(kp)
                 except RuntimeError: # Handle potential issues if kp is already gone
                    print(f"Warning: Could not remove keyframe at {kp.co.x} for {data_path}[{index}]")
                    pass # Continue if removal fails for some reason
            return fcurve

        # --- 获取或创建所有需要的 F-Curves ---
        try:
            loc_fcurves = {i: get_or_create_fcurve_and_clear_range(loc_data_path, i) for i in range(3)}
            rot_fcurves = {i: get_or_create_fcurve_and_clear_range(rot_data_path, i) for i in rot_indices}
        except Exception as e: # Catch potential errors during F-Curve handling
            traceback.print_exc()
            self.report({'ERROR'}, f"处理 F-Curves 时出错: {e}")
            return {'CANCELLED'}

        # --- 记录应用跟踪前的初始状态 ---
        # Evaluate at the frame *before* the tracking starts, or scene start
        eval_frame = max(scene.frame_start, start_frame - 1)
        if start_frame <= scene.frame_start:
            eval_frame = scene.frame_start

        original_frame = scene.frame_current
        scene.frame_set(eval_frame)
        context.view_layer.update() # Ensure evaluated state is calculated

        # Store initial LOCAL transforms for reference in some modes
        initial_local_location = cam.location.copy()
        initial_local_rotation = getattr(cam, rot_data_path).copy()
        initial_world_matrix = cam.matrix_world.copy() # Store initial world matrix too

        scene.frame_set(original_frame) # Restore original frame immediately
        context.view_layer.update()

        # --- 缓存目标的骨骼（如果需要）---
        target_bone = None
        if target.type == 'ARMATURE' and props.tracking_bone_target:
            target_bone = target.pose.bones.get(props.tracking_bone_target)
            if not target_bone:
                self.report({'WARNING'}, f"在骨架 '{target.name}' 中未找到骨骼 '{props.tracking_bone_target}', 将跟踪骨架原点")
                # Do not cancel, just track the armature origin instead

        # 逐帧处理
        try:
            for frame in range(start_frame, end_frame + 1):
                scene.frame_set(frame)

                context.view_layer.update()
                if target_bone:
                
                    try:
                         target_pos_world = target.matrix_world @ target_bone.head
                    except (ReferenceError, AttributeError):
       
                         target_pos_world = target.matrix_world.translation.copy()
                         target_bone = None 
                         print(f"Warning: Target bone lost at frame {frame}, tracking armature origin.")
                else:
                    target_pos_world = target.matrix_world.translation.copy()

                current_cam_world_loc = cam.matrix_world.translation.copy()


                new_cam_world_pos = current_cam_world_loc
                rot_quat_world = cam.matrix_world.to_quaternion() 

                if tracking_mode == 'ROTATION_ONLY':

                    new_cam_world_pos = initial_world_matrix.translation

                    direction_world = target_pos_world - new_cam_world_pos
                    if direction_world.length > 0.0001:

                        rot_quat_world = direction_world.to_track_quat('-Z', 'Y')
                    else:

                        rot_quat_world = initial_world_matrix.to_quaternion()

                elif tracking_mode == 'POSITION_ONLY':

                    if frame == start_frame:

                         initial_target_pos_world = target_pos_world.copy()
                         world_offset_vector = initial_world_matrix.translation - initial_target_pos_world

                    new_cam_world_pos = target_pos_world + world_offset_vector
                    # Keep the initial world orientation
                    rot_quat_world = initial_world_matrix.to_quaternion()

                elif tracking_mode == 'ORBIT_AND_TRACK':
                    duration = end_frame - start_frame
                    progress = (frame - start_frame) / duration if duration > 0 else 0


                    if props.orbit_revolutions > 0 and duration > 0:
                        angle = progress * 2 * math.pi * props.orbit_revolutions
                    else:
                        angle = (frame - start_frame) * math.radians(props.orbit_speed)
                    vertical_angle = math.radians(props.orbit_vertical_angle)
                    radius = props.orbit_radius
                    offset_x = radius * math.cos(angle) * math.cos(vertical_angle)
                    offset_y = radius * math.sin(angle) * math.cos(vertical_angle)
                    offset_z = radius * math.sin(vertical_angle)
                    orbit_offset_world = mathutils.Vector((offset_x, offset_y, offset_z))


                    new_cam_world_pos = target_pos_world + orbit_offset_world


                    look_at_world = target_pos_world - new_cam_world_pos
                    if look_at_world.length > 0.0001:
                        rot_quat_world = look_at_world.to_track_quat('-Z', 'Y')
                    else:
            
                         if frame > start_frame and rot_fcurves: 
                             try:
                    
                                 if rot_data_path == 'rotation_quaternion':
                                     last_rot_val = [rot_fcurves.get(i).evaluate(frame - 1) for i in rot_indices if rot_fcurves.get(i)]
                                     if len(last_rot_val) == 4: 
                                         rot_quat_world = mathutils.Quaternion(last_rot_val).normalized()
                                     else:
                                          rot_quat_world = initial_world_matrix.to_quaternion()
                                 else: 
                                     last_rot_val = [rot_fcurves.get(i).evaluate(frame - 1) for i in rot_indices if rot_fcurves.get(i)]
                                     if len(last_rot_val) == 3:
                                          rot_quat_world = mathutils.Euler(last_rot_val, rot_mode).to_quaternion()
                                     else:
                                          rot_quat_world = initial_world_matrix.to_quaternion()
                             except: 
                                rot_quat_world = initial_world_matrix.to_quaternion()
                         else:
                             rot_quat_world = initial_world_matrix.to_quaternion()



                target_world_matrix = (mathutils.Matrix.Translation(new_cam_world_pos) @
                                       rot_quat_world.to_matrix().to_4x4())

                if cam.parent:
                    local_matrix = cam.matrix_parent_inverse @ target_world_matrix
                else:
                    local_matrix = target_world_matrix 

                cam.location = local_matrix.to_translation()
                if rot_data_path == 'rotation_quaternion':
                    cam.rotation_quaternion = local_matrix.to_quaternion().normalized()
                else:
                    cam.rotation_euler = local_matrix.to_euler(rot_mode)

                cam.keyframe_insert(data_path=loc_data_path, frame=frame)
                cam.keyframe_insert(data_path=rot_data_path, frame=frame)

        except Exception as e:
            traceback.print_exc()
            self.report({'ERROR'}, f"应用跟踪时出错: {e}")
 
            scene.frame_set(original_frame)
            context.view_layer.update()
            return {'CANCELLED'}

            scene.frame_set(original_frame)
            context.view_layer.update()

        all_fcurves = list(loc_fcurves.values()) + list(rot_fcurves.values())
        for fc in all_fcurves:
            if not fc or not fc.keyframe_points:
                 continue
            for kp in fc.keyframe_points:

                if start_frame <= kp.co.x <= end_frame:
                    kp.interpolation = 'BEZIER' 
                    kp.handle_left_type = 'AUTO'
                    kp.handle_right_type = 'AUTO'
            try:
                fc.update() 
            except Exception as e:
                 print(f"Warning: Could not update fcurve {fc.data_path}[{fc.array_index}]: {e}")

        self.report({'INFO'}, f"已为摄像机 '{cam.name}' 应用跟踪动画 [{start_frame}-{end_frame}]")
        return {'FINISHED'}
class ANIM_PT_CameraAnimation(bpy.types.Panel):
    bl_label = "摄像机动画工具"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "动画工具" # 和主面板保持一致

    @classmethod
    def poll(cls, context):
        # 只在 active_panel 为 CAMERA 时显示
        return context.scene.anim_merge_props.active_panel == 'CAMERA'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        props = scene.camera_anim_props # 获取摄像机动画属性

        active_obj = context.active_object

        # 检查是否选中了摄像机
        if not active_obj or active_obj.type != 'CAMERA':
            box = layout.box()
            box.label(text="请先选择一个摄像机对象", icon='ERROR')
            return


        shake_box = layout.box()
        shake_box.label(text="摄像机抖动", icon='SHADERFX') # 使用一个看起来像效果的图标

        # 抖动帧范围
        row = shake_box.row(align=True)
        row.prop(props, "shake_frame_start", text="起始帧")
        row.prop(props, "shake_frame_end", text="结束帧")

        # 抖动类型
        shake_box.prop(props, "shake_type", text="类型")

        # 抖动影响开关
        row = shake_box.row(align=True)
        row.prop(props, "shake_use_position", text="位置", toggle=True)
        row.prop(props, "shake_use_rotation", text="旋转", toggle=True)

        # 抖动幅度 - 根据开关启用/禁用
        row = shake_box.row(align=True)
        pos_col = row.column(align=True)
        pos_col.enabled = props.shake_use_position # 启用/禁用位置幅度
        pos_col.prop(props, "shake_amount_pos", text="幅度")

        rot_col = row.column(align=True)
        rot_col.enabled = props.shake_use_rotation # 启用/禁用旋转幅度
        rot_col.prop(props, "shake_amount_rot", text="幅度")

        # 抖动频率
        shake_box.prop(props, "shake_frequency", text="频率")

        # 应用抖动按钮
        shake_box.operator("anim.apply_camera_shake", text="应用抖动效果", icon='PLAY')

        # --- 摄像机跟踪功能区 ---
        track_box = layout.box()
        track_box.label(text="摄像机跟踪", icon='CONSTRAINT')

        # 跟踪帧范围
        row = track_box.row(align=True)
        row.prop(props, "tracking_frame_start", text="起始帧")
        row.prop(props, "tracking_frame_end", text="结束帧")

        # 跟踪目标选择
        track_box.prop(props, "tracking_target", text="目标物体")

        # 如果目标是骨架，显示骨骼名称输入
        if props.tracking_target and props.tracking_target.type == 'ARMATURE':
            row = track_box.row(align=True)
            # 添加骨骼搜索功能 (可以使用现有的骨骼搜索，或者简单文本输入)
            row.prop(props, "tracking_bone_target", text="骨骼名称")
            # 可以考虑加一个按钮从骨架中选择骨骼

        # 跟踪模式
        track_box.prop(props, "tracking_mode", text="模式")

        # 应用跟踪按钮 - 正确设置启用状态
        track_button_row = track_box.row()
        track_button_row.enabled = bool(props.tracking_target) # 在行上设置 enabled
        track_button_row.operator("anim.apply_camera_tracking", text="应用跟踪效果", icon='PLAY')

        # 在绘制跟踪设置的部分
        if props.tracking_mode == 'ORBIT_AND_TRACK':
            track_box.prop(props, "orbit_radius")
            track_box.prop(props, "orbit_revolutions")
            track_box.prop(props, "orbit_speed")
            track_box.prop(props, "orbit_vertical_angle")

class ANIM_OT_FetchOnlineActions(bpy.types.Operator):
    bl_idname = "anim.fetch_online_actions"
    bl_label = "获取在线动作"
    bl_description = "连接到服务器并获取在线动作列表"
    
    def execute(self, context):
        props = context.scene.anim_merge_props
        
        # 设置加载标志
        props.is_loading_actions = True
        
        # 清空现有列表
        props.online_actions.clear()
        
        # 创建线程获取数据，避免阻塞 UI
        threading.Thread(target=self.fetch_data_thread, args=(context,)).start()
        
        return {'FINISHED'}
    
    def fetch_data_thread(self, context):
        props = context.scene.anim_merge_props
        url = f"{props.server_url}/api/actions"
        
        try:
            # 处理可能的 SSL 证书问题
            ssl._create_default_https_context = ssl._create_unverified_context
            
            # 发送请求
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                # 在主线程中处理数据更新 UI
                def update_ui():
                    # 将获取的动作添加到列表中
                    if 'actions' in data and isinstance(data['actions'], list):
                        for action_data in data['actions']:
                            item = props.online_actions.add()
                            item.id = action_data.get('id', 0)
                            item.name = action_data.get('name', '未命名')
                            item.description = action_data.get('description', '')
                            item.author = action_data.get('author', '未知')
                            item.timestamp = action_data.get('timestamp', '')
                            item.download_url = action_data.get('download_url', '')
                    
                    props.is_loading_actions = False
                    self.report({'INFO'}, f"成功获取 {len(data.get('actions', []))} 个在线动作")
                
                # 在主线程中安全更新 UI
                bpy.app.timers.register(update_ui, first_interval=0.1)
                
        except HTTPError as e:
            def report_http_error():
                props.is_loading_actions = False
                self.report({'ERROR'}, f"HTTP错误：{e.code} {e.reason}")
            bpy.app.timers.register(report_http_error, first_interval=0.1)
            
        except URLError as e:
            def report_url_error():
                props.is_loading_actions = False
                self.report({'ERROR'}, f"连接错误：{e.reason}")
            bpy.app.timers.register(report_url_error, first_interval=0.1)
            
        except Exception as e:
            def report_error():
                props.is_loading_actions = False
                self.report({'ERROR'}, f"获取动作列表失败：{str(e)}")
            bpy.app.timers.register(report_error, first_interval=0.1)

# 新增：下载动作文件的操作符
class ANIM_OT_DownloadOnlineAction(bpy.types.Operator):
    bl_idname = "anim.download_online_action"
    bl_label = "下载动作"
    bl_description = "下载选中的动作到动作库"
    
    action_index: bpy.props.IntProperty()
    
    def execute(self, context):
        props = context.scene.anim_merge_props
        
        # 索引检查
        if self.action_index < 0 or self.action_index >= len(props.online_actions):
            self.report({'ERROR'}, "动作索引无效")
            return {'CANCELLED'}
        
        # 获取选中的动作
        action = props.online_actions[self.action_index]
        
        # 使用固定的下载目录 LIBRARY_PATH / download
        download_folder = LIBRARY_PATH / "download"
        
        # 确保目录存在
        os.makedirs(download_folder, exist_ok=True)
        
        # 创建下载线程，避免阻塞UI
        threading.Thread(target=self.download_thread, 
                         args=(action, download_folder, context)).start()
        
        return {'FINISHED'}
    
    def download_thread(self, action, download_folder, context):
        try:
            # 处理可能的 SSL 证书问题
            ssl._create_default_https_context = ssl._create_unverified_context
            
            # 构建保存路径
            safe_name = "".join([c for c in action.name if c.isalnum() or c in " _-."]).strip()
            filename = f"{safe_name}_{action.id}.json"
            save_path = os.path.join(download_folder, filename)
            
            # 下载文件
            urllib.request.urlretrieve(action.download_url, save_path)
            
            # 在主线程中自动导入到动作库
            def import_to_library():
                try:
                    # 使用反序列化函数加载动作
                    action_obj = deserialize_action(save_path)
                    if not action_obj:
                        self.report({'ERROR'}, "动作文件格式错误")
                        return
                    
                    # 获取动作的帧范围
                    frames = []
                    for fcurve in action_obj.fcurves:
                        frames.extend([kp.co.x for kp in fcurve.keyframe_points])
                    frame_range = (int(min(frames)), int(max(frames))) if frames else (1, 250)
                    
                    # 准备保存路径
                    safe_name = clean_filename(action_obj.name)
                    fbx_stem = "Online_Actions"  # 专门的分类
                    save_dir = LIBRARY_PATH / fbx_stem
                    save_dir.mkdir(exist_ok=True)
                    final_path = save_dir / f"{safe_name}.json"
                    counter = 1
                    while final_path.exists():
                        final_path = save_dir / f"{safe_name}_{counter}.json"
                        counter += 1
                    
                    # 复制JSON文件到库目录
                    with open(save_path, 'r', encoding='utf-8') as src_file:
                        action_data = json.load(src_file)
                        with open(final_path, 'w', encoding='utf-8') as dest_file:
                            json.dump(action_data, dest_file, indent=2, ensure_ascii=False)
                    
                    # 添加到动作库
                    item = context.scene.anim_merge_props.action_library_items.add()
                    item.name = action_obj.name
                    item.filepath = str(final_path)
                    item.source_fbx = fbx_stem
                    item.frame_range = frame_range
                    
                    # 清理临时动作
                    bpy.data.actions.remove(action_obj)
                    
                    # 可选：删除临时下载的文件
                    # os.remove(save_path)
                    
                    self.report({'INFO'}, f"动作 '{action.name}' 已自动导入到动作库")
                    
                    # 刷新动作库
                    load_library_actions()
                    
                except Exception as e:
                    self.report({'ERROR'}, f"导入动作库失败：{str(e)}")
            
            bpy.app.timers.register(import_to_library, first_interval=0.1)
            
        except Exception as e:
            def report_error():
                self.report({'ERROR'}, f"下载失败：{str(e)}")
            bpy.app.timers.register(report_error, first_interval=0.1)

# 新增：显示动作详情的操作符
class ANIM_OT_ShowOnlineActionDetails(bpy.types.Operator):
    bl_idname = "anim.show_online_action_details"
    bl_label = "动作详情"
    bl_description = "显示动作的详细信息"
    
    action_index: bpy.props.IntProperty()
    
    def execute(self, context):
        return {'FINISHED'}
    
    def invoke(self, context, event):
        props = context.scene.anim_merge_props
        
        # 索引检查
        if self.action_index < 0 or self.action_index >= len(props.online_actions):
            self.report({'ERROR'}, "动作索引无效")
            return {'CANCELLED'}
        
        # 获取选中的动作
        action = props.online_actions[self.action_index]
        
        return context.window_manager.invoke_props_dialog(self, width=400)
    
    def draw(self, context):
        props = context.scene.anim_merge_props
        action = props.online_actions[self.action_index]
        
        layout = self.layout
        
        # 显示动作详情
        box = layout.box()
        row = box.row()
        row.label(text=f"名称: {action.name}")
        
        row = box.row()
        row.label(text=f"作者: {action.author}")
        
        row = box.row()
        row.label(text=f"上传时间: {action.timestamp[:16].replace('T', ' ')}")
        
        box.separator()
        
        # 使用 label 显示多行文本
        box.label(text="描述:")
        desc_lines = action.description.split("\n")
        for line in desc_lines[:4]:  # 最多显示4行
            box.label(text=line)
        
        if len(desc_lines) > 4:
            box.label(text="...")
        
        layout.separator()
        
        # 下载按钮
        row = layout.row()
        row.operator(ANIM_OT_DownloadOnlineAction.bl_idname, text="下载到动作库").action_index = self.action_index
        
class ANIM_PT_OnlineLibraryPanel(bpy.types.Panel):
    bl_label = "在线动作库"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "动画工具"
    
    @classmethod
    def poll(cls, context):
        # 只在 active_panel 为 ONLINE 时显示
        return context.scene.anim_merge_props.active_panel == 'ONLINE'
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.anim_merge_props  # 使用正确的属性名
        
        # 服务器地址
        row = layout.row()
        row.prop(props, "server_url")
        
        # 刷新按钮
        row = layout.row()
        row.operator(ANIM_OT_FetchOnlineActions.bl_idname, text="刷新动作列表", icon='FILE_REFRESH')
        
        # 加载状态指示
        if props.is_loading_actions:
            row = layout.row()
            row.label(text="加载中...", icon='SORTTIME')
        
        # 显示动作列表
        if len(props.online_actions) > 0:
            layout.separator()
            layout.label(text=f"共 {len(props.online_actions)} 个在线动作:")
            
            # 动作列表
            for i, action in enumerate(props.online_actions):
                box = layout.box()
                row = box.row()
                
                # 显示动作名称和创建者
                split = row.split(factor=0.7)
                col1 = split.column()
                col1.label(text=f"{action.name}")
                
                col2 = split.column()
                col2.label(text=f"作者: {action.author}")
                
                # 动作操作按钮
                row = box.row()
                row.operator(ANIM_OT_ShowOnlineActionDetails.bl_idname, text="详情").action_index = i
                row.operator(ANIM_OT_DownloadOnlineAction.bl_idname, text="下载并导入").action_index = i

classes = (
    ActionLibraryItem,
    BoneSelectionItem,
    AnimMergeProperties,
    ANIM_OT_ImportToLibrary,
    ANIM_OT_UseLibraryAction,
    ANIM_OT_UseLibraryActionWithBones,
    ANIM_OT_MergeFBX,
    ANIM_OT_MergeFBXWithBones,
    ANIM_OT_SaveCustomAction,
    ANIM_OT_SelectBones,
    ANIM_OT_SelectAllBones,
    ANIM_OT_ExportAction,
    ANIM_OT_ImportActionFromJSON,
    ANIM_OT_CheckForUpdates,
    ANIM_OT_InstallUpdate,
    ANIM_OT_ListFBXBones,
    ANIM_OT_ShowBonesDialog,
    ANIM_OT_CopyBoneName,
    ANIM_OT_SearchBone,
    ANIM_OT_SelectBoneItem,
    ANIM_OT_SelectSearchedBone,
    ANIM_OT_ConfirmBoneSelection,
    ANIM_OT_ReloadBoneMapping,
    ANIM_OT_AutoBoneMapping,
    ANIM_OT_ApplyProgressiveOffset,
    ANIM_OT_ApplyFixedOrientation,
    ANIM_OT_DeleteLibraryAction,
    ANIM_OT_CreateLibraryFolder,
    ANIM_OT_DeleteLibraryFolder,
    ANIM_UL_ActionLibrary,
    ANIM_PT_ActionLibrary,
    ANIM_PT_MergeControl,
    ANIM_PT_UpdatePanel,
    PathAnimationProperties,
    ANIM_OT_CreateBezierPath,
    ANIM_OT_ApplyPathAnimation,
    ANIM_OT_ApplyControlPoints,
    ANIM_PT_PathAnimation,
    BreathAnimationProperties,
    ANIM_OT_ApplyBreathAnimation,
    ANIM_PT_BreathAnimation,
    CameraAnimationProperties,
    ANIM_OT_ApplyCameraShake, # 新增抖动操作符
    ANIM_OT_ApplyCameraTracking, # 新增跟踪操作符 (占位)
    ANIM_PT_CameraAnimation,
    ANIM_PT_MainPanel,
    OnlineActionItem,
    ANIM_OT_FetchOnlineActions,
    ANIM_OT_DownloadOnlineAction,
    ANIM_OT_ShowOnlineActionDetails,
    ANIM_PT_OnlineLibraryPanel,
)

def load_library_actions():
    try:
        if LIBRARY_PATH.exists():
            for scene in bpy.data.scenes:
                scene.anim_merge_props.action_library_items.clear()
                
                for category_dir in LIBRARY_PATH.glob("*"):
                    if category_dir.is_dir():
                        for action_file in category_dir.glob("*.json"):
                            try:
                                with open(action_file, 'r', encoding='utf-8') as f:
                                    data = json.load(f)
                                    frames = []
                                    for curve in data['fcurves']:
                                        frames.extend([k['frame'] for k in curve['keyframes']])
                                    frame_range = (int(min(frames)), int(max(frames))) if frames else (1, 250)
                                    
                                    item = scene.anim_merge_props.action_library_items.add()
                                    item.name = data.get('name', action_file.stem)
                                    item.filepath = str(action_file)
                                    item.source_fbx = category_dir.name
                                    item.frame_range = frame_range
                            except Exception as e:
                                print(f"加载动作失败:{action_file}，错误:{str(e)}")
    except Exception as e:
        print(f"初始化动作库失败:{str(e)}")

def register():
    # 先注册用作属性类型的类
    bpy.utils.register_class(OnlineActionItem)
    bpy.utils.register_class(ActionLibraryItem)
    bpy.utils.register_class(BoneSelectionItem)
    
    # 然后注册依赖这些类型的属性组
    bpy.utils.register_class(AnimMergeProperties)
    bpy.utils.register_class(PathAnimationProperties)
    bpy.utils.register_class(BreathAnimationProperties)
    bpy.utils.register_class(CameraAnimationProperties)
    
    # 注册其他所有类
    for cls in classes:
        # 跳过已经注册的类
        if cls in [OnlineActionItem, ActionLibraryItem, BoneSelectionItem, 
                  AnimMergeProperties, PathAnimationProperties, 
                  BreathAnimationProperties, CameraAnimationProperties]:
            continue
        try:
            bpy.utils.register_class(cls)
        except ValueError as e:
            print(f"注册类时出错 {cls.__name__}: {e}")

    # 添加属性到场景
    bpy.types.Scene.anim_merge_props = bpy.props.PointerProperty(type=AnimMergeProperties)
    bpy.types.Scene.bone_selection = bpy.props.CollectionProperty(type=BoneSelectionItem)
    bpy.types.Scene.path_anim_props = bpy.props.PointerProperty(type=PathAnimationProperties)
    bpy.types.Scene.breath_anim_props = bpy.props.PointerProperty(type=BreathAnimationProperties)
    bpy.types.Scene.camera_anim_props = bpy.props.PointerProperty(type=CameraAnimationProperties)
    
    # 避免重复注册 timer
    if not bpy.app.timers.is_registered(load_library_actions):
        bpy.app.timers.register(load_library_actions, first_interval=1.0)

    # 自动检查更新 (使用全局标志避免重复注册)
    global _startup_timer_registered
    if not _startup_timer_registered:
        def check_updates_on_startup():
            if not bpy.context.window_manager.progress_begin:
                # 确保在主线程中执行检查
                if hasattr(bpy.context.scene, 'anim_merge_props') and bpy.context.scene.anim_merge_props.auto_check_update:
                    check_for_updates(auto_check=True)
                return None
            return 1.0 # 继续等待 Blender 加载完成

        bpy.app.timers.register(check_updates_on_startup, first_interval=3.0)
        _startup_timer_registered = True

def unregister():
    # 移除属性 (注意顺序)
    # 使用 try-except 块来处理属性可能不存在的情况
    try:
        if hasattr(bpy.types.Scene, 'camera_anim_props'):
            del bpy.types.Scene.camera_anim_props
    except AttributeError:
        pass
    try:
        if hasattr(bpy.types.Scene, 'breath_anim_props'):
            del bpy.types.Scene.breath_anim_props
    except AttributeError:
        pass
    try:
        if hasattr(bpy.types.Scene, 'path_anim_props'):
            del bpy.types.Scene.path_anim_props
    except AttributeError:
        pass
    try:
        if hasattr(bpy.types.Scene, 'bone_selection'):
            del bpy.types.Scene.bone_selection
    except AttributeError:
        pass
    try:
        if hasattr(bpy.types.Scene, 'anim_merge_props'):
            del bpy.types.Scene.anim_merge_props
    except AttributeError:
        pass

    # 卸载在线动作库相关类
    try:
        bpy.utils.unregister_class(ANIM_PT_OnlineLibraryPanel)
        bpy.utils.unregister_class(ANIM_OT_ShowOnlineActionDetails)
        bpy.utils.unregister_class(ANIM_OT_DownloadOnlineAction)
        bpy.utils.unregister_class(ANIM_OT_FetchOnlineActions)
    except RuntimeError as e:
        print(f"卸载在线动作库相关类时出错: {e}")
    
    # 卸载其他所有类
    for cls in reversed(classes):
        if cls in [OnlineActionItem, ActionLibraryItem, BoneSelectionItem, 
                  AnimMergeProperties, PathAnimationProperties, 
                  BreathAnimationProperties, CameraAnimationProperties]:
            continue
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            # 忽略卸载错误，可能已被其他脚本卸载
            print(f"无法卸载类 {cls.__name__}, 可能已被卸载")
    
    try:
        bpy.utils.unregister_class(CameraAnimationProperties)
        bpy.utils.unregister_class(BreathAnimationProperties)
        bpy.utils.unregister_class(PathAnimationProperties)
        bpy.utils.unregister_class(AnimMergeProperties)
        
        bpy.utils.unregister_class(BoneSelectionItem)
        bpy.utils.unregister_class(ActionLibraryItem)
        bpy.utils.unregister_class(OnlineActionItem)
    except RuntimeError as e:
        print(f"卸载属性类型类时出错: {e}")

    # 卸载定时器 (如果可能)
    if bpy.app.timers.is_registered(load_library_actions):
        bpy.app.timers.unregister(load_library_actions)

    # 重置启动定时器标志
    global _startup_timer_registered
    _startup_timer_registered = False


if __name__ == "__main__":
    # 确保在卸载旧版本后再注册新版本 (用于开发测试)
    # try:
    #     unregister()
    # except Exception as e:
    #     print(f"卸载旧版本时出错: {e}")
    register()
