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

bl_info = {
    "name": "FBX动画导入器",
    "author": "ΔIng KMnO4",
    "version": (1, 8, 22),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Animation",
    "description": "骨骼动画管理工具 Dev 1.8.22",
    "category": "Animation"
}
LIBRARY_PATH = Path.home() / "AniTools"
LIBRARY_PATH.mkdir(parents=True, exist_ok=True)

# 更新服务器设置
UPDATE_SERVER = "http://47.97.203.23:8010"
UPDATE_CHECK_URL = f"{UPDATE_SERVER}/check_update"
UPDATE_CHECK_INTERVAL = 86400  # 默认每天检查一次（秒）
last_update_check = 0
update_available = False
latest_version = None
download_url = None
update_message = ""

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
        name="显示映射帮助",
        default=False,
        description="显示关于骨骼映射的帮助信息"
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

class ANIM_PT_ActionLibrary(bpy.types.Panel):
    bl_label = "动作资产库"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "动画工具"
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.anim_merge_props
        
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
    bl_options = {'DEFAULT_CLOSED'}
    
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
    ANIM_OT_ApplyProgressiveOffset, # Add new operator class
    ANIM_OT_ApplyFixedOrientation,  # Add the new fixed orientation operator
    ANIM_OT_DeleteLibraryAction,    # Add the delete action operator
    ANIM_OT_CreateLibraryFolder,    # Add create folder operator
    ANIM_OT_DeleteLibraryFolder,    # Add delete folder operator
    ANIM_UL_ActionLibrary,
    ANIM_PT_ActionLibrary,
    ANIM_PT_MergeControl,
    ANIM_PT_UpdatePanel
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
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.anim_merge_props = bpy.props.PointerProperty(type=AnimMergeProperties)
    bpy.types.Scene.bone_selection = bpy.props.CollectionProperty(type=BoneSelectionItem)
    bpy.app.timers.register(load_library_actions, first_interval=1.0)
    
    # 自动检查更新
    def check_updates_on_startup():
        # 等待 Blender 完全加载
        if bpy.context.scene and hasattr(bpy.context.scene, 'anim_merge_props'):
            if bpy.context.scene.anim_merge_props.auto_check_update:
                check_for_updates(auto_check=True)
            return None  # 不再调用定时器
        return 3.0  # 继续等待
    
    bpy.app.timers.register(check_updates_on_startup, first_interval=3.0)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.Scene, 'anim_merge_props'):    
        del bpy.types.Scene.anim_merge_props
    if hasattr(bpy.types.Scene, 'bone_selection'):
        del bpy.types.Scene.bone_selection

if __name__ == "__main__":
    register()
