/*
  The 3D model viewer, inside the sealed frame (api/tree.py `sealed_viewer`).

  A preview of a model's shape, written here rather than vendored: it reads
  STL (binary and text), OBJ, and glTF 2.0 (a .glb, or a .gltf that carries
  its data in itself) -- triangles, each material's base colour and its base
  colour picture -- and turns the model with the pointer. It does not do
  animation, skinning, lights, the other maps of a material, or compressed
  geometry (Draco, meshopt); a model that needs one of those says so and is
  offered as a download by the page around it.

  Like the PDF viewer it runs in a document that is an opaque origin with no
  network, and is handed the file's bytes in one message. Nothing from the
  file is put into markup.
*/
(function () {
  "use strict";

  var root = document.getElementById("view");
  var MAX_TRIANGLES = 2000000;
  var done = false;

  function note(text) {
    var p = document.createElement("p");
    p.className = "note";
    p.textContent = text;
    root.replaceChildren(p);
  }

  function theme(colours) {
    Object.keys(colours || {}).forEach(function (name) {
      if (/^[a-z]+$/.test(name) && /^[#(),.%\s\w-]{1,40}$/.test(colours[name])) {
        document.documentElement.style.setProperty("--" + name, colours[name]);
      }
    });
  }

  function Refused(why) { this.why = why; }

  /* A colour of the theme as numbers, for a model that names none of its
     own: whatever CSS the page around sent, a canvas says what it comes to. */
  function plain() {
    var paper = document.createElement("canvas");
    paper.width = paper.height = 1;
    var pen = paper.getContext("2d");
    pen.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--muted") || "gray";
    pen.fillRect(0, 0, 1, 1);
    var dot = pen.getImageData(0, 0, 1, 1).data;
    return [dot[0] / 255, dot[1] / 255, dot[2] / 255, 1];
  }

  /* --- A model, as groups of triangles -------------------------------------
     { positions: Float32Array (xyz per corner), normals?, uvs?, colour: [r,g,b,a],
       image?: {bytes, type} } -- never indexed: three corners a triangle. */

  function stl(buffer) {
    var view = new DataView(buffer);
    var count = buffer.byteLength >= 84 ? view.getUint32(80, true) : 0;
    if (buffer.byteLength >= 84 && 84 + count * 50 === buffer.byteLength) {
      if (count > MAX_TRIANGLES) count = MAX_TRIANGLES;
      var positions = new Float32Array(count * 9);
      for (var i = 0; i < count; i++) {
        var at = 84 + i * 50 + 12;
        for (var k = 0; k < 9; k++) positions[i * 9 + k] = view.getFloat32(at + k * 4, true);
      }
      return [{ positions: positions, colour: null }];
    }
    var text = new TextDecoder().decode(buffer);
    var found = [];
    var number = /vertex\s+(\S+)\s+(\S+)\s+(\S+)/g;
    var match;
    while ((match = number.exec(text)) && found.length < MAX_TRIANGLES * 9) {
      found.push(parseFloat(match[1]), parseFloat(match[2]), parseFloat(match[3]));
    }
    found.length -= found.length % 9;
    if (!found.length) throw new Refused("failed");
    return [{ positions: new Float32Array(found), colour: null }];
  }

  function obj(buffer) {
    var lines = new TextDecoder().decode(buffer).split("\n");
    var points = [];
    var out = [];
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (line.charCodeAt(0) === 118 && line.charCodeAt(1) === 32) {          // "v "
        var p = line.trim().split(/\s+/);
        points.push(parseFloat(p[1]), parseFloat(p[2]), parseFloat(p[3]));
      } else if (line.charCodeAt(0) === 102 && line.charCodeAt(1) === 32) {   // "f "
        var f = line.trim().split(/\s+/);
        var corners = [];
        for (var c = 1; c < f.length; c++) {
          var index = parseInt(f[c], 10);
          if (!index) continue;
          corners.push(index < 0 ? points.length / 3 + index : index - 1);
        }
        for (var n = 1; n + 1 < corners.length; n++) {
          if (out.length >= MAX_TRIANGLES * 9) break;
          [corners[0], corners[n], corners[n + 1]].forEach(function (corner) {
            out.push(points[corner * 3] || 0, points[corner * 3 + 1] || 0, points[corner * 3 + 2] || 0);
          });
        }
      }
    }
    if (!out.length) throw new Refused("failed");
    return [{ positions: new Float32Array(out), colour: null }];
  }

  /* --- glTF 2.0 ------------------------------------------------------------- */
  var SIZES = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 };
  var READ = {
    5120: ["getInt8", 1, 127], 5121: ["getUint8", 1, 255], 5122: ["getInt16", 2, 32767],
    5123: ["getUint16", 2, 65535], 5125: ["getUint32", 4, 1], 5126: ["getFloat32", 4, 1]
  };

  function multiply(a, b) {
    var out = new Array(16);
    for (var c = 0; c < 4; c++) for (var r = 0; r < 4; r++) {
      out[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
    return out;
  }

  function local(node) {
    if (Array.isArray(node.matrix) && node.matrix.length === 16) return node.matrix;
    var t = node.translation || [0, 0, 0], q = node.rotation || [0, 0, 0, 1], s = node.scale || [1, 1, 1];
    var x = q[0], y = q[1], z = q[2], w = q[3];
    return [
      (1 - 2 * (y * y + z * z)) * s[0], 2 * (x * y + z * w) * s[0], 2 * (x * z - y * w) * s[0], 0,
      2 * (x * y - z * w) * s[1], (1 - 2 * (x * x + z * z)) * s[1], 2 * (y * z + x * w) * s[1], 0,
      2 * (x * z + y * w) * s[2], 2 * (y * z - x * w) * s[2], (1 - 2 * (x * x + y * y)) * s[2], 0,
      t[0], t[1], t[2], 1
    ];
  }

  function gltf(json, binary) {
    var needs = json.extensionsRequired || [];
    for (var e = 0; e < needs.length; e++) {
      if (/draco|meshopt|quantization/i.test(String(needs[e]))) throw new Refused("unsupported");
    }
    var buffers = (json.buffers || []).map(function (entry, index) {
      if (entry.uri === undefined && index === 0 && binary) return binary;
      var uri = String(entry.uri || "");
      var data = /^data:[^,]*;base64,(.*)$/.exec(uri);
      if (!data) throw new Refused("external");   // another file: there is no network here
      var raw = atob(data[1]);
      var bytes = new Uint8Array(raw.length);
      for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
      return bytes.buffer;
    });

    function span(index) {
      var view = (json.bufferViews || [])[index];
      if (!view || !buffers[view.buffer]) throw new Refused("failed");
      return { buffer: buffers[view.buffer], offset: view.byteOffset || 0, length: view.byteLength, stride: view.byteStride || 0 };
    }

    function accessor(index, normalise) {
      var a = (json.accessors || [])[index];
      if (!a || a.bufferView === undefined || a.sparse) throw new Refused("unsupported");
      var how = READ[a.componentType], width = SIZES[a.type];
      if (!how || !width) throw new Refused("failed");
      var from = span(a.bufferView);
      var view = new DataView(from.buffer, from.offset + (a.byteOffset || 0));
      var stride = from.stride || how[1] * width;
      var out = new Float32Array(a.count * width);
      var scale = (normalise || a.normalized) && how[2] !== 1 ? 1 / how[2] : 1;
      for (var i = 0; i < a.count; i++) for (var k = 0; k < width; k++) {
        out[i * width + k] = view[how[0]](i * stride + k * how[1], true) * scale;
      }
      return out;
    }

    function picture(material) {
      var pbr = (material && material.pbrMetallicRoughness) || {};
      var texture = pbr.baseColorTexture ? (json.textures || [])[pbr.baseColorTexture.index] : null;
      var image = texture ? (json.images || [])[texture.source] : null;
      if (!image || image.bufferView === undefined) return null;
      if (!/^image\/(png|jpeg|webp)$/.test(String(image.mimeType))) return null;
      var from = span(image.bufferView);
      return { bytes: new Uint8Array(from.buffer, from.offset, from.length), type: image.mimeType };
    }

    var groups = [];
    var triangles = 0;

    function mesh(index, matrix) {
      var found = (json.meshes || [])[index];
      ((found && found.primitives) || []).forEach(function (primitive) {
        if ((primitive.mode === undefined ? 4 : primitive.mode) !== 4) return;
        var attributes = primitive.attributes || {};
        if (attributes.POSITION === undefined || triangles >= MAX_TRIANGLES) return;
        var position = accessor(attributes.POSITION);
        var uv = attributes.TEXCOORD_0 !== undefined ? accessor(attributes.TEXCOORD_0, true) : null;
        var order = primitive.indices !== undefined ? accessor(primitive.indices) : null;
        var count = order ? order.length : position.length / 3;
        count -= count % 3;
        count = Math.min(count, (MAX_TRIANGLES - triangles) * 3);
        triangles += count / 3;
        var positions = new Float32Array(count * 3);
        var uvs = uv ? new Float32Array(count * 2) : null;
        for (var i = 0; i < count; i++) {
          var v = order ? order[i] : i;
          var x = position[v * 3], y = position[v * 3 + 1], z = position[v * 3 + 2];
          positions[i * 3] = matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12];
          positions[i * 3 + 1] = matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13];
          positions[i * 3 + 2] = matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14];
          if (uvs) { uvs[i * 2] = uv[v * 2]; uvs[i * 2 + 1] = uv[v * 2 + 1]; }
        }
        var material = (json.materials || [])[primitive.material];
        var factor = material && material.pbrMetallicRoughness && material.pbrMetallicRoughness.baseColorFactor;
        groups.push({
          positions: positions,
          uvs: uvs,
          colour: Array.isArray(factor) && factor.length === 4 ? factor : null,
          image: uvs ? picture(material) : null
        });
      });
    }

    function walk(index, parent, depth) {
      var node = (json.nodes || [])[index];
      if (!node || depth > 64) return;
      var matrix = multiply(parent, local(node));
      if (node.mesh !== undefined) mesh(node.mesh, matrix);
      (node.children || []).forEach(function (child) { walk(child, matrix, depth + 1); });
    }

    var identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
    var scene = (json.scenes || [])[json.scene || 0];
    var roots = scene ? scene.nodes || [] : (json.nodes || []).map(function (_n, i) { return i; });
    roots.forEach(function (index) { walk(index, identity, 0); });
    if (!groups.length) throw new Refused("failed");
    return groups;
  }

  function glb(buffer) {
    var view = new DataView(buffer);
    if (buffer.byteLength < 20 || view.getUint32(0, true) !== 0x46546c67) throw new Refused("failed");
    if (view.getUint32(4, true) !== 2) throw new Refused("unsupported");
    var at = 12, json = null, binary = null;
    while (at + 8 <= buffer.byteLength) {
      var length = view.getUint32(at, true), kind = view.getUint32(at + 4, true);
      var body = buffer.slice(at + 8, at + 8 + length);
      if (kind === 0x4e4f534a) json = JSON.parse(new TextDecoder().decode(body));
      else if (kind === 0x004e4942 && !binary) binary = body;
      at += 8 + length;
    }
    if (!json) throw new Refused("failed");
    return gltf(json, binary);
  }

  function read(format, buffer) {
    if (format === "stl") return stl(buffer);
    if (format === "obj") return obj(buffer);
    if (format === "gltf") return gltf(JSON.parse(new TextDecoder().decode(buffer)), null);
    return glb(buffer);
  }

  /* --- Drawing --------------------------------------------------------------- */
  function flat(positions) {
    var normals = new Float32Array(positions.length);
    for (var i = 0; i + 8 < positions.length; i += 9) {
      var ax = positions[i + 3] - positions[i], ay = positions[i + 4] - positions[i + 1], az = positions[i + 5] - positions[i + 2];
      var bx = positions[i + 6] - positions[i], by = positions[i + 7] - positions[i + 1], bz = positions[i + 8] - positions[i + 2];
      var nx = ay * bz - az * by, ny = az * bx - ax * bz, nz = ax * by - ay * bx;
      var size = Math.hypot(nx, ny, nz) || 1;
      for (var k = 0; k < 9; k += 3) {
        normals[i + k] = nx / size; normals[i + k + 1] = ny / size; normals[i + k + 2] = nz / size;
      }
    }
    return normals;
  }

  var VERTEX = "attribute vec3 p;attribute vec3 n;attribute vec2 t;uniform mat4 m;uniform mat3 r;" +
    "varying vec3 vn;varying vec2 vt;void main(){vn=r*n;vt=t;gl_Position=m*vec4(p,1.0);}";
  var FRAGMENT = "precision mediump float;varying vec3 vn;varying vec2 vt;uniform vec4 c;" +
    "uniform sampler2D s;uniform float u;void main(){vec4 b=c;if(u>0.5)b*=texture2D(s,vt);" +
    "float l=0.35+0.65*abs(normalize(vn).z);gl_FragColor=vec4(b.rgb*l,1.0);}";

  function show(message) {
    theme(message.theme);
    var words = message.words || {};
    var groups;
    try {
      groups = read(message.format, message.bytes);
    } catch (problem) {
      note(words[problem instanceof Refused ? problem.why : "failed"] || words.failed || "");
      document.documentElement.setAttribute("data-failed", problem instanceof Refused ? problem.why : "failed");
      return;
    }

    var canvas = document.createElement("canvas");
    canvas.className = "model";
    root.replaceChildren(canvas);
    var gl = canvas.getContext("webgl", { antialias: true, preserveDrawingBuffer: true });
    if (!gl) { note(words.nowebgl || words.failed || ""); return; }

    function shader(kind, source) {
      var made = gl.createShader(kind);
      gl.shaderSource(made, source);
      gl.compileShader(made);
      return made;
    }
    var program = gl.createProgram();
    gl.attachShader(program, shader(gl.VERTEX_SHADER, VERTEX));
    gl.attachShader(program, shader(gl.FRAGMENT_SHADER, FRAGMENT));
    gl.linkProgram(program);
    gl.useProgram(program);
    var at = { p: gl.getAttribLocation(program, "p"), n: gl.getAttribLocation(program, "n"), t: gl.getAttribLocation(program, "t") };
    var uniform = {};
    ["m", "r", "c", "s", "u"].forEach(function (name) { uniform[name] = gl.getUniformLocation(program, name); });

    var low = [Infinity, Infinity, Infinity], high = [-Infinity, -Infinity, -Infinity];
    var count = 0;
    groups.forEach(function (group) {
      for (var i = 0; i < group.positions.length; i++) {
        var value = group.positions[i];
        if (value < low[i % 3]) low[i % 3] = value;
        if (value > high[i % 3]) high[i % 3] = value;
      }
      count += group.positions.length / 9;
      function fill(data) {
        var made = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, made);
        gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
        return made;
      }
      group.p = fill(group.positions);
      group.n = fill(flat(group.positions));
      group.t = group.uvs ? fill(group.uvs) : null;
      group.size = group.positions.length / 3;
      if (group.image && window.createImageBitmap) {
        createImageBitmap(new Blob([group.image.bytes], { type: group.image.type })).then(function (bitmap) {
          var side = Math.min(2048, gl.getParameter(gl.MAX_TEXTURE_SIZE));
          var paper = document.createElement("canvas");
          paper.width = paper.height = side;   // a power of two: WebGL 1 repeats nothing else
          paper.getContext("2d").drawImage(bitmap, 0, 0, side, side);
          group.texture = gl.createTexture();
          gl.bindTexture(gl.TEXTURE_2D, group.texture);
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, paper);
          gl.generateMipmap(gl.TEXTURE_2D);
          paint();
        }).catch(function () { /* its colour stays */ });
      }
      group.positions = group.uvs = null;
    });
    var centre = [(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, (low[2] + high[2]) / 2];
    var radius = Math.hypot(high[0] - low[0], high[1] - low[1], high[2] - low[2]) / 2 || 1;
    var yaw = 0.6, pitch = 0.35, zoom = 1;
    var fallback = plain();

    function paint() {
      var ratio = Math.min(window.devicePixelRatio || 1, 2);
      var width = Math.max(1, Math.floor(canvas.clientWidth * ratio));
      var height = Math.max(1, Math.floor(canvas.clientHeight * ratio));
      if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
      gl.viewport(0, 0, width, height);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST);

      var cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
      // The model turned (yaw about its up, then pitch), then pushed back and projected.
      var turn = [cy, sp * sy, -cp * sy, 0, 0, cp, sp, 0, sy, -sp * cy, cp * cy, 0, 0, 0, 0, 1];
      var scale = 1 / radius;
      var away = 3.2 * zoom;
      var f = 1 / Math.tan(0.4), aspect = width / height, near = 0.05, far = 50;
      var project = [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) / (near - far), -1, 0, 0, 2 * far * near / (near - far), 0];
      var place = [scale, 0, 0, 0, 0, scale, 0, 0, 0, 0, scale, 0, -centre[0] * scale, -centre[1] * scale, -centre[2] * scale, 1];
      var back = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, -away, 1];
      gl.uniformMatrix4fv(uniform.m, false, new Float32Array(multiply(project, multiply(back, multiply(turn, place)))));
      gl.uniformMatrix3fv(uniform.r, false, new Float32Array([turn[0], turn[1], turn[2], turn[4], turn[5], turn[6], turn[8], turn[9], turn[10]]));

      groups.forEach(function (group) {
        gl.bindBuffer(gl.ARRAY_BUFFER, group.p);
        gl.enableVertexAttribArray(at.p);
        gl.vertexAttribPointer(at.p, 3, gl.FLOAT, false, 0, 0);
        gl.bindBuffer(gl.ARRAY_BUFFER, group.n);
        gl.enableVertexAttribArray(at.n);
        gl.vertexAttribPointer(at.n, 3, gl.FLOAT, false, 0, 0);
        if (group.t && at.t >= 0) {
          gl.bindBuffer(gl.ARRAY_BUFFER, group.t);
          gl.enableVertexAttribArray(at.t);
          gl.vertexAttribPointer(at.t, 2, gl.FLOAT, false, 0, 0);
        } else if (at.t >= 0) {
          gl.disableVertexAttribArray(at.t);
        }
        gl.uniform4fv(uniform.c, new Float32Array(group.colour || fallback));
        gl.uniform1f(uniform.u, group.texture ? 1 : 0);
        if (group.texture) { gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, group.texture); gl.uniform1i(uniform.s, 0); }
        gl.drawArrays(gl.TRIANGLES, 0, group.size);
      });
      document.documentElement.setAttribute("data-triangles", String(count));
    }

    var held = null;
    canvas.addEventListener("pointerdown", function (event) {
      held = [event.clientX, event.clientY];
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", function (event) {
      if (!held) return;
      yaw += (event.clientX - held[0]) * 0.01;
      pitch = Math.max(-1.5, Math.min(1.5, pitch + (event.clientY - held[1]) * 0.01));
      held = [event.clientX, event.clientY];
      paint();
    });
    ["pointerup", "pointercancel"].forEach(function (name) {
      canvas.addEventListener(name, function () { held = null; });
    });
    canvas.addEventListener("wheel", function (event) {
      event.preventDefault();
      zoom = Math.max(0.3, Math.min(6, zoom * (event.deltaY > 0 ? 1.1 : 0.9)));
      paint();
    }, { passive: false });
    window.addEventListener("resize", paint);
    if (window.ResizeObserver) new ResizeObserver(paint).observe(canvas);

    var hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = (words.hint || "") + (count >= MAX_TRIANGLES ? " " + (words.cut || "") : "");
    root.appendChild(hint);
    paint();
  }

  window.addEventListener("message", function (event) {
    var message = event.data;
    if (done || event.source !== window.parent || !message || message.lembasView !== "model3d") return;
    done = true;
    try {
      show(message);
    } catch (_problem) {
      note((message.words && message.words.failed) || "");
      document.documentElement.setAttribute("data-failed", "failed");
    }
  });
  /* Said until answered: the page around may not be listening yet. */
  var asked = 0;
  (function ready() {
    if (done || asked++ > 40) return;
    window.parent.postMessage({ lembasViewerReady: "model3d" }, "*");
    setTimeout(ready, 500);
  })();
})();
