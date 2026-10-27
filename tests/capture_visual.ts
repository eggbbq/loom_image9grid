// Isolated actual-project validation of the supplied panel and poster textures.
@IEditorEnv.regClass()
class NineGridVisualEvidence {
    static capture() {
        const fs = require("fs");
        const path = require("path");
        const root = EditorEnv.projectPath;
        const scene = new Laya.Scene();
        scene.name = "NineGridVisualValidation";
        scene.size(800, 1340);
        const cases = [
            {file: "ui/商城_给程序/切的/图层 68.png", name: "Panel68", x: 20, y: 20, w: 575, h: 172},
            {file: "ui/商城_给程序/切的/组 37.png", name: "Poster37", x: 20, y: 220, w: 738, h: 1078}
        ];
        for (const item of cases) {
            const meta = JSON.parse(fs.readFileSync(path.join(root, "assets", item.file + ".meta"), "utf8"));
            const image = new Laya.GImage();
            image.name = item.name;
            image.autoSize = false;
            image.src = "res://" + meta.uuid;
            image.pos(item.x, item.y);
            image.size(item.w, item.h);
            scene.addChild(image);
        }
        fs.writeFileSync(path.join(root, "assets/Scene.ls"),
            JSON.stringify(IEditorEnv.HierarchyWriter.writeScene(scene, null), null, 2) + "\n");
        scene.destroy(true);
    }
}
