// Test-only native IDE serializer. Run in an isolated LayaAir 3.4.1 project.
@IEditorEnv.regClass()
class NineGridEvidence {
    static capture() {
        const fs = require("fs");
        const path = require("path");
        const root = EditorEnv.projectPath;
        const meta = JSON.parse(fs.readFileSync(path.join(root, "assets/panel.png.meta"), "utf8"));
        const image = new Laya.GImage();
        image.name = "Panel";
        image.autoSize = false;
        image.src = "res://" + meta.uuid;
        image.pos(12, 14);
        image.size(600, 300);
        const scene = new Laya.Scene();
        scene.name = "NineGridValidation";
        scene.size(640, 360);
        scene.addChild(image);
        fs.writeFileSync(path.join(root, "assets/Scene.ls"),
            JSON.stringify(IEditorEnv.HierarchyWriter.writeScene(scene, null), null, 2) + "\n");
        scene.destroy(true);
    }
}
