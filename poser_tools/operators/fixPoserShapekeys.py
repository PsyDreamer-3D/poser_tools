import bpy
from .functionsShapeKeys import consolidation_steps, consolidate_poser_shapekeys


class OT_FixPoserShapekeys_Operator(bpy.types.Operator):
    bl_idname = "poser.fix_poser_shapekeys"
    bl_label = "Fix Poser Shapekeys"
    bl_options = {'REGISTER', 'UNDO'}

    # Modal state — set as class defaults, overwritten per-instance in invoke().
    _timer = None
    _gen = None
    _total = 0
    _step = 0
    _obj = None

    @classmethod
    def poll(cls, context):
        if context.active_object is None or context.active_object.type != 'MESH':
            return False
        return True

    # ------------------------------------------------------------------
    # Modal path (interactive use)
    # ------------------------------------------------------------------

    def invoke(self, context, event):
        obj = context.active_object
        if obj.get("morphs_consolidated"):
            self.report({'INFO'}, "Morphs are already consolidated")
            return {'CANCELLED'}

        shapekeys = obj.data.shape_keys.key_blocks
        options = context.scene.poser_shapekeys_addon

        gen = consolidation_steps(obj, shapekeys, options.is_daz)
        self._total = next(gen)   # first yield is the step count
        self._gen = gen
        self._step = 0
        self._obj = obj

        wm = context.window_manager
        wm.progress_begin(0, max(self._total, 1))
        self._timer = wm.event_timer_add(0.001, window=context.window)
        wm.modal_handler_add(self)

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        try:
            next(self._gen)
            self._step += 1
            context.window_manager.progress_update(self._step)
            return {'RUNNING_MODAL'}
        except StopIteration:
            return self._finish(context)

    def _finish(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        wm.progress_end()
        self._obj["morphs_consolidated"] = True
        self.report({'INFO'}, "Morphs consolidated")
        return {'FINISHED'}

    def cancel(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            context.window_manager.progress_end()

    # ------------------------------------------------------------------
    # Synchronous path (Blender's redo system calls execute() directly)
    # ------------------------------------------------------------------

    def execute(self, context):
        obj = context.active_object
        if obj.get("morphs_consolidated"):
            return {'CANCELLED'}

        shapekeys = obj.data.shape_keys.key_blocks
        options = context.scene.poser_shapekeys_addon
        consolidate_poser_shapekeys(obj, shapekeys, options.is_daz)
        obj["morphs_consolidated"] = True
        return {'FINISHED'}
