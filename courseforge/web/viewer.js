/* The 3D viewer. The only ES module in the app.
 *
 * app.js stays a classic script; this crosses the module boundary exactly once by
 * publishing window.BlendViewer. Loaded via the import map in index.html, so
 * three.js is served from vendor/ and the app still works with no internet.
 */
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

let S = null;   // the single live viewer, or null

/* Blender's solid viewport is a matte mid grey, not the white Principled
   default. That white, under a room map plus two more lights, clips to a
   flat highlight and the form disappears. */
const PREVIEW_GREY = 0x8d9196;
const CLAY = new THREE.MeshStandardMaterial({ color: PREVIEW_GREY, roughness: 1, metalness: 0 });
const NORMALS = new THREE.MeshNormalMaterial();

function isNearWhite(color) {
  return color && color.r > 0.82 && color.g > 0.82 && color.b > 0.82;
}

/* A material with no image is the "they did not pack a texture" case.
   Leave a real color (a red plastic, a blue glass) alone. Turn the white
   default into the same grey clay uses, so Default and Textured agree. */
function softenUntextured(root) {
  root.traverse(obj => {
    if (!obj.isMesh) return;
    const mats = Array.isArray(obj.material) ? obj.material : (obj.material ? [obj.material] : []);
    mats.forEach(mat => {
      if (!mat || !mat.color || mat.map) return;
      if (!isNearWhite(mat.color)) return;
      mat.color.setHex(PREVIEW_GREY);
      if ('roughness' in mat) mat.roughness = 1;
      if ('metalness' in mat) mat.metalness = 0;
      if ('emissive' in mat && isNearWhite(mat.emissive)) mat.emissive.setHex(0x000000);
      mat.needsUpdate = true;
    });
  });
}

function teardown() {
  if (!S) return;
  cancelAnimationFrame(S.raf);
  window.removeEventListener('resize', S.onResize);
  if (S.controls) S.controls.dispose();
  if (S.root) {
    S.root.traverse(o => {
      if (o.geometry) o.geometry.dispose();
      const mats = Array.isArray(o.material) ? o.material : (o.material ? [o.material] : []);
      mats.forEach(m => {
        Object.values(m).forEach(v => { if (v && v.isTexture) v.dispose(); });
        m.dispose();
      });
    });
  }
  if (S.pmrem) S.pmrem.dispose();
  if (S.renderer) {
    S.renderer.dispose();
    // Without this the WebGL context stays alive. Chrome caps live contexts near
    // 16, and the roster is keyboard-navigable, so a few seconds of j/k would
    // silently exhaust them and every later viewer would render black.
    S.renderer.forceContextLoss();
    if (S.renderer.domElement && S.renderer.domElement.parentNode) {
      S.renderer.domElement.parentNode.removeChild(S.renderer.domElement);
    }
  }
  S = null;
}

function frameObject(root, camera, controls) {
  const box = new THREE.Box3().setFromObject(root);
  if (box.isEmpty()) return { radius: 1, center: new THREE.Vector3() };
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size.length() / 2, 1e-4);

  // Student scenes turn up at 0.001 and at 400 units. Three's defaults
  // (near 0.1, far 2000) would clip the whole model out of existence.
  camera.near = radius / 1000;
  camera.far = radius * 200;
  camera.updateProjectionMatrix();

  const dir = new THREE.Vector3(1, 0.65, 1).normalize();
  const dist = radius / Math.sin((camera.fov * Math.PI / 180) / 2) * 1.25;
  camera.position.copy(center).add(dir.multiplyScalar(dist));
  controls.target.copy(center);
  controls.update();
  return { radius, center };
}

const BlendViewer = {
  /** Mount a .glb into hostEl. Any previous viewer is disposed first. */
  async mount(hostEl, url, opts = {}) {
    teardown();
    if (!hostEl) return;
    hostEl.innerHTML = '<div class="viewerBusy">loading model…</div>';

    const width = Math.max(hostEl.clientWidth || 480, 240);
    const height = Math.max(opts.height || 340, 200);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(width, height, false);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(opts.background || 0x1c2026);

    // We export with export_lights=False, so without an environment map any
    // metallic material renders black. The room is only a soft fill. A second
    // pair of full-white lights on top of it clipped every grey to white.
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environmentIntensity = 0.4;
    scene.add(new THREE.HemisphereLight(0xc5c9d0, 0x2a3038, 0.45));
    const key = new THREE.DirectionalLight(0xe7ebf0, 0.65);
    key.position.set(3, 5, 4);
    scene.add(key);

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.01, 1000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    S = { renderer, scene, camera, controls, pmrem, root: null, raf: 0,
          wireframe: false, mode: 'textured', helpers: null, host: hostEl };

    let gltf;
    try {
      gltf = await new GLTFLoader().loadAsync(url);
    } catch (err) {
      const raw = String((err && err.message) || err);
      // The usual cause is a .blend submitted without its texture files, which
      // makes Blender emit texture entries with no source. Say that instead of
      // surfacing a TypeError from deep inside the loader.
      const friendly = /reading 'undefined'|images\[/.test(raw)
        ? 'This model has texture references with no image data, usually because '
          + 'the .blend was submitted without its texture folder. Re-run the '
          + 'Blender pass to rebuild the preview, or open the file in Blender. '
          + 'The scene report below is unaffected.'
        : raw.slice(0, 200);
      hostEl.innerHTML = '<div class="viewerBusy bad">' + friendly + '</div>';
      teardown();
      return;
    }
    if (!S) return;                       // disposed while loading

    const root = gltf.scene || gltf.scenes[0];
    softenUntextured(root);
    S.root = root;
    scene.add(root);
    const { radius, center } = frameObject(root, camera, controls);

    // Grid and axes make scale and origin problems legible, and both are
    // things an intro modeling rubric actually scores.
    const helpers = new THREE.Group();
    const grid = new THREE.GridHelper(radius * 4, 20, 0x50596a, 0x333a46);
    grid.position.y = center.y - radius;
    helpers.add(grid);
    helpers.add(new THREE.AxesHelper(radius * 1.2));
    scene.add(helpers);
    S.helpers = helpers;

    hostEl.innerHTML = '';
    hostEl.appendChild(renderer.domElement);

    let tris = 0;
    root.traverse(o => { if (o.isMesh && o.geometry) {
      const g = o.geometry;
      tris += (g.index ? g.index.count : (g.attributes.position ? g.attributes.position.count : 0)) / 3;
    }});
    if (opts.onReady) opts.onReady({ triangles: Math.round(tris), radius });

    S.onResize = () => {
      if (!S) return;
      const w = Math.max(hostEl.clientWidth || width, 240);
      S.camera.aspect = w / height;
      S.camera.updateProjectionMatrix();
      S.renderer.setSize(w, height, false);
    };
    window.addEventListener('resize', S.onResize);

    const tick = () => {
      if (!S) return;
      S.raf = requestAnimationFrame(tick);
      S.controls.update();
      S.renderer.render(S.scene, S.camera);
    };
    tick();
  },

  setMode(mode) {
    if (!S) return;
    S.mode = mode;
    S.scene.overrideMaterial = mode === 'clay' ? CLAY : (mode === 'normals' ? NORMALS : null);
    if (S.scene.overrideMaterial) S.scene.overrideMaterial.wireframe = S.wireframe;
  },

  toggleWireframe() {
    if (!S) return false;
    S.wireframe = !S.wireframe;
    if (S.scene.overrideMaterial) S.scene.overrideMaterial.wireframe = S.wireframe;
    S.root.traverse(o => {
      const mats = Array.isArray(o.material) ? o.material : (o.material ? [o.material] : []);
      mats.forEach(m => { m.wireframe = S.wireframe; });
    });
    return S.wireframe;
  },

  toggleHelpers() {
    if (!S || !S.helpers) return false;
    S.helpers.visible = !S.helpers.visible;
    return S.helpers.visible;
  },

  reset() {
    if (!S || !S.root) return;
    frameObject(S.root, S.camera, S.controls);
  },

  dispose: teardown,
  get active() { return !!S; },
};

window.BlendViewer = BlendViewer;
