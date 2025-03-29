import bpy
import os
import json
import re
from pathlib import Path

bl_info = {
    "name": "FBX动画导入器",
    "author": "KMNO4",
    "version": (1, 4),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Animation",
    "description": "支持循环的FBX动画导入工具",
    "category": "Animation"
}

LIBRARY_PATH = Path.home() / "AniTools"
LIBRARY_PATH.mkdir(parents=True, exist_ok=True)

def clean_filename(name):
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip('_')[:120]

class ActionLibraryItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="动作名称")
    filepath: bpy.props.StringProperty(name="文件路径")
    source_fbx: bpy.props.StringProperty(name="来源FBX")
    frame_range: bpy.props.IntVectorProperty(size=2, name="帧范围")

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
    action_library_index: bpy.props.IntProperty(default=0)
    action_library_items: bpy.props.CollectionProperty(type=ActionLibraryItem)
    show_library: bpy.props.BoolProperty(default=False, name="显示动作库")

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
    merged_action = target_action.copy() if target_action else bpy.data.actions.new("Merged_Action")
    duration = source_range[1] - source_range[0]
    
    for loop in range(loop_times):
        current_offset = time_offset + loop * duration
        
        for src_fcurve in source_action.fcurves:
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
            
            existing_keys = {k.co.x: k for k in target_fcurve.keyframe_points}
            
            for src_key in src_fcurve.keyframe_points:
                frame = src_key.co.x
                if not (source_range[0] <= frame <= source_range[1]):
                    continue
                    
                target_frame = frame - source_range[0] + current_offset
                
                if merge_mode == 'REPLACE':
                    if target_frame in existing_keys:
                        target_fcurve.keyframe_points.remove(existing_keys[target_frame])
                    new_key = target_fcurve.keyframe_points.insert(
                        target_frame, src_key.co.y
                    )
                    new_key.interpolation = src_key.interpolation
                    
                elif merge_mode == 'MIX':
                    if target_frame in existing_keys:
                        existing_val = existing_keys[target_frame].co.y
                        new_val = (existing_val + src_key.co.y) * 0.5
                        existing_keys[target_frame].co.y = new_val
                    else:
                        new_key = target_fcurve.keyframe_points.insert(
                            target_frame, src_key.co.y
                        )
                        new_key.interpolation = src_key.interpolation
            
            target_fcurve.update()
    
    return merged_action

class ANIM_OT_ImportToLibrary(bpy.types.Operator):
    """将FBX动画导入动作库"""
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
        action = deserialize_action(Path(item.filepath))
        if not action:
            self.report({'ERROR'}, "动作加载失败")
            return {'CANCELLED'}
        target = context.active_object
        if not target or target.type != 'ARMATURE':
            self.report({'ERROR'}, "选骨架!!!")
            return {'CANCELLED'}
        props.source_start = item.frame_range[0]
        props.source_end = item.frame_range[1]
        merged_action = merge_actions(
            target.animation_data.action if target.animation_data else None,
            action,
            props.target_start,
            (props.source_start, props.source_end),
            props.merge_mode,
            props.loop_times
        )
        if not target.animation_data:
            target.animation_data_create()
        target.animation_data.action = merged_action
        context.scene.frame_end = max(
            context.scene.frame_end,
            props.target_start + (props.source_end - props.source_start) * props.loop_times
        )
        self.report({'INFO'}, f"OK: {item.name}")
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
        
        action = load_fbx_animation(self.filepath)
        if not action:
            self.report({'ERROR'}, "读取失败")
            return {'CANCELLED'}
        merged_action = merge_actions(
            target.animation_data.action if target.animation_data else None,
            action,
            props.target_start,
            (props.source_start, props.source_end),
            props.merge_mode,
            props.loop_times
        )
        if not target.animation_data:
            target.animation_data_create()
        target.animation_data.action = merged_action
        context.scene.frame_end = max(
            context.scene.frame_end,
            props.target_start + (props.source_end - props.source_start) * props.loop_times
        )
        self.report({'INFO'}, "合并完成!")
        return {'FINISHED'}
    
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class ANIM_UL_ActionLibrary(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row(align=True)
        row.label(text=item.name, icon='ACTION')
        sub = row.row()
        sub.alignment = 'RIGHT'
        sub.label(text=f"{item.source_fbx} [{item.frame_range[0]}-{item.frame_range[1]}]")

class ANIM_PT_ActionLibrary(bpy.types.Panel):
    bl_label = "动作资产库"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "动画工具"
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.anim_merge_props
        header = layout.row()
        header.operator("anim.import_to_library", text="导入FBX", icon='ADD')
        header.prop(props, "show_library", 
            icon='DOWNARROW_HLT' if props.show_library else 'RIGHTARROW',
            emboss=False
        )
        
        if props.show_library:
            box = layout.box()
            box.template_list(
                "ANIM_UL_ActionLibrary", "",
                props, "action_library_items",
                props, "action_library_index"
            )
            row = box.row()
            row.operator("anim.use_library_action", text="应用", icon='PLAY')

class ANIM_PT_MergeControl(bpy.types.Panel):
    bl_label = "FBX动画合并"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "动画工具"
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.anim_merge_props
        
        box = layout.box()
        box.label(text="直接合并FBX", icon='FILE')
        box.operator("anim.merge_fbx", text="选择FBX并合并")
        
        box = layout.box()
        box.label(text="时间轴对齐", icon='PREVIEW_RANGE')
        box.prop(props, "source_start")
        box.prop(props, "source_end")
        box.prop(props, "loop_times")
        box.label(text="↓FBX动画从选中骨骼动画的第几帧开始↓", icon='ARROW_LEFTRIGHT')
        box.prop(props, "target_start")
        
        box = layout.box()
        box.label(text="合并策略", icon='MODIFIER')
        box.row().prop(props, "merge_mode", expand=True)
        
        box = layout.box()
        box.label(text="当前状态", icon='INFO')
        obj = context.active_object
        if obj and obj.type == 'ARMATURE':
            box.label(text=f"目标骨架: {obj.name}", icon='ARMATURE_DATA')
            if obj.animation_data and obj.animation_data.action:
                action = obj.animation_data.action
                frame_count = sum(len(fc.keyframe_points) for fc in action.fcurves)
                box.label(text=f"关键帧总数: {frame_count}")
                box.label(text=f"帧范围: {int(action.frame_range[0])}-{int(action.frame_range[1])}")
            else:
                box.label(text="没有检测到动画", icon='KEYFRAME_HLT')
        else:
            box.label(text="请选择骨架", icon='ERROR')

classes = (
    ActionLibraryItem,
    AnimMergeProperties,
    ANIM_OT_ImportToLibrary,
    ANIM_OT_UseLibraryAction,
    ANIM_OT_MergeFBX,
    ANIM_UL_ActionLibrary,
    ANIM_PT_ActionLibrary,
    ANIM_PT_MergeControl
)

def load_library_actions():
    try:
        if LIBRARY_PATH.exists():
            for scene in bpy.data.scenes:
                scene.anim_merge_props.action_library_items.clear()
                
                for fbx_dir in LIBRARY_PATH.glob("*"):
                    if fbx_dir.is_dir():
                        for action_file in fbx_dir.glob("*.json"):
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
                                    item.source_fbx = fbx_dir.name
                                    item.frame_range = frame_range
                            except Exception as e:
                                print(f"加载动作失败:{action_file}，错误:{str(e)}")
    except Exception as e:
        print(f"初始化动作库失败:{str(e)}")

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.anim_merge_props = bpy.props.PointerProperty(type=AnimMergeProperties)
    bpy.app.timers.register(load_library_actions, first_interval=1.0)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.Scene, 'anim_merge_props'):
        del bpy.types.Scene.anim_merge_props

if __name__ == "__main__":
    register()
