(function () {
  var csrf = "";
  var selected = "";
  var lastEventId = "";
  var localRows = [];
  var viewerOffset = 0;
  var panel = "dashboard";
  var titles = {
    dashboard: ["Tổng quan", "Sự kiện đi qua Python Bridge. Số Roblox chỉ hiện khi có dữ liệu thật."],
    viewers: ["Người xem", "Danh sách lấy từ phiên mock trên bridge."],
    likes: ["Tim", "500 tim mở khóa câu trong phép chiếu bridge."],
    comments: ["Bình luận", "Lệnh được xếp vào hàng đợi hiện có."],
    gifts: ["Quà", "Xu mock. Một nút, một sự kiện."],
    baits: ["Mồi", "Năm cấp đã cấu hình. Không ghi đè và không đổi tỷ lệ cá."],
    fishing: ["Câu cá", "Force chỉ ở MOCK_MODE và không được coi là ACK Roblox."],
    camera: ["Camera", "Đi qua sự kiện mà CameraDirector hiểu, hoặc ghi nhận là chưa tới game."],
    leaderboard: ["Bảng xếp hạng", "Không tạo hệ điểm thứ hai."],
    performance: ["Hiệu năng", "RSS và CPU là số đo process Python. Roblox để trống nếu chưa có telemetry."],
    logs: ["Nhật ký", "Tối đa các sự kiện gần nhất. Không ghi secret."],
    settings: ["Cài đặt", "Reset phiên mock và các bài test có nút dừng."]
  };

  function $(id) { return document.getElementById(id); }

  function toast(text, kind) {
    var node = $("toast");
    node.textContent = text || "";
    node.className = "toast " + (kind || "");
  }

  function api(path, options) {
    var opts = options || {};
    opts.credentials = "same-origin";
    opts.headers = opts.headers || {};
    if (opts.body) {
      opts.method = "POST";
      opts.headers["Content-Type"] = "application/json";
      opts.headers["X-CSRF-Token"] = csrf;
    }
    return fetch(path, opts).then(function (response) {
      return response.json().then(function (data) {
        data._status = response.status;
        return data;
      }).catch(function () {
        return { ok: false, _status: response.status, detail: "Phản hồi không đọc được." };
      });
    });
  }

  function showResult(node, data) {
    if (!data) return;
    var text = data.detail || data.note || data.error || "";
    if (data.robloxLabel) text += " · " + data.robloxLabel;
    if (data.bridgeStatus) text += " · Bridge " + data.bridgeStatus;
    if (data.robloxStatus) text += " · Roblox " + data.robloxStatus;
    node.textContent = text;
    node.className = "feedback " + (data.ok === false || data._status >= 400 ? "bad-text" : "ok-text");
    toast(text, data.ok === false ? "bad-text" : "ok-text");
    if (data.eventId) lastEventId = data.eventId;
    if (data.viewer) paintUnlock(data.viewer);
    if (data.progress) $("like-progress").textContent = data.progress;
    if (data.unlockText) $("like-unlock").textContent = data.unlockText;
  }

  function paintUnlock(viewer) {
    var likes = viewer && viewer.likes ? viewer.likes : 0;
    $("like-progress").textContent = likes + " / 500";
    $("like-unlock").textContent = likes >= 500 ? "ĐÃ MỞ KHÓA CÂU CÁ!" : "";
  }

  function rememberSend(action, username) {
    localRows.unshift({
      time: Date.now() / 1000,
      username: username || selected || "",
      eventType: action,
      eventId: "",
      status: "SENT",
      evidence: "observed-browser",
      summary: action
    });
    localRows = localRows.slice(0, 100);
  }

  function missing(value) {
    return value === null || value === undefined ? "Chưa có dữ liệu" : String(value);
  }

  function card(label, value, tone) {
    return '<article class="stat ' + (tone || "") + '"><span>' + label + '</span><b>' + value + '</b></article>';
  }

  function paintPills(board) {
    var bridge = board && board.bridge && board.bridge.ok ? "ok" : "bad";
    var roblox = board && board.roblox && board.roblox.connected ? "ok" : "bad";
    var mode = board && board.mockMode ? "warn" : "bad";
    var label = board && board.roblox ? board.roblox.label : "ROBLOX DISCONNECTED";
    var session = board && board.sessionOn ? "ok" : "warn";
    var mirror = board && board.mirror ? board.mirror : null;
    var mirrorTone = mirror && mirror.enabled ? (mirror.lastError ? "bad" : "ok") : "warn";
    var mirrorText = mirror && mirror.enabled ? "MIRROR " + (mirror.queued || 0) + " CHỜ" : "MIRROR TẮT";
    // In mirror mode Roblox polls Render, so this process cannot see its pulls.
    var robloxText = mirror && mirror.enabled ? "ROBLOX QUA RENDER" : label;
    var robloxTone = mirror && mirror.enabled ? "warn" : roblox;
    $("pills").innerHTML =
      '<span class="pill ' + bridge + '">PYTHON BRIDGE</span>' +
      '<span class="pill ' + robloxTone + '">' + robloxText + '</span>' +
      '<span class="pill ' + mode + '">' + (board && board.mockMode ? "MOCK MODE" : "LIVE") + '</span>' +
      '<span class="pill ' + mirrorTone + '">' + mirrorText + '</span>' +
      '<span class="pill ' + session + '">' + (board && board.sessionOn ? "PHIÊN TEST" : "PHIÊN DỪNG") + '</span>';
  }

  function paintDashboard(board) {
    if (!board || !board.bridge) {
      $("cards").innerHTML = card("PYTHON BRIDGE", "MẤT KẾT NỐI", "bad");
      paintPills(null);
      return;
    }
    paintPills(board);
    var rss = board.process && board.process.rssBytes;
    var cpu = board.process && board.process.cpuPercent;
    var ack = board.roblox.lastAckAt ? "CÓ ACK" : "CHƯA CÓ ACK";
    var tunnel = board.httpsTunnel && board.httpsTunnel.label ? board.httpsTunnel.label : "KHÔNG CHẠY";
    var mirror = board.mirror || {};
    var mirrorValue = mirror.enabled
      ? (mirror.sent || 0) + " gửi · " + (mirror.failed || 0) + " lỗi"
      : "TẮT";
    var mirrorTone = mirror.enabled ? (mirror.lastError ? "bad" : "ok") : "warn";
    $("cards").innerHTML =
      card("PYTHON BRIDGE", "ĐANG CHẠY", "ok") +
      card("HTTPS TUNNEL", tunnel, "bad") +
      card("ROBLOX CONNECTION", board.roblox.label, board.roblox.connected ? "ok" : "bad") +
      card("MOCK MODE", board.mockMode ? "BẬT" : "TẮT", board.mockMode ? "gold" : "bad") +
      card("EVENT QUEUE", board.bridge.depth + " / " + board.bridge.capacity) +
      card("ACK STATUS", ack, board.roblox.lastAckAt ? "ok" : "bad") +
      card("CURRENT SESSION", board.bridge.sessionId.slice(0, 8)) +
      card("TOTAL VIEWERS", board.viewers, "ok") +
      card("ACTIVE NPC", missing(board.activeNpc)) +
      card("TOTAL LIKES", board.totalLikes, "gold") +
      card("TOTAL GIFTS", board.totalGifts, "gold") +
      card("TOTAL FISH", missing(board.totalFish)) +
      card("TOTAL MISSES", missing(board.totalMisses)) +
      card("PYTHON RSS", rss ? Math.round(rss / 1048576) + " MB" : "Chưa có dữ liệu") +
      card("PYTHON CPU", cpu === null || cpu === undefined ? "Đang lấy mẫu" : cpu + " %") +
      card("AUTO DEMO", board.autoDemo && board.autoDemo.running ? "ĐANG CHẠY" : "DỪNG", board.autoDemo && board.autoDemo.running ? "gold" : "") +
      card("RENDER MIRROR", mirrorValue, mirrorTone);
    $("projection").textContent = board.projectionNote || "";
    var warn = $("roblox-warn");
    if (board.roblox.connected) {
      warn.hidden = true;
    } else if (mirror && mirror.enabled) {
      warn.hidden = false;
      warn.textContent = "Roblox kéo sự kiện từ Render qua mirror. Process local này không thấy lượt kéo đó, nên trạng thái kết nối không đo được ở đây.";
    } else {
      warn.hidden = false;
      warn.textContent = "Roblox bridge đang tắt. Sự kiện dừng ở Python. Trang này không bật HTTP.";
    }
    if (board.stress && board.stress.running) {
      $("stress-feedback").textContent = "Đang chạy " + board.stress.kind + " · đã gửi " + board.stress.sent;
    }
    var demo = board.autoDemo || {};
    var demoNode = $("auto-demo-feedback");
    if (demoNode) {
      if (demo.running) {
        demoNode.textContent = "AUTO DEMO đang chạy · vòng " + (demo.wave || 0) + " · " + (demo.sent || 0) + " sự kiện · " + (demo.step || "");
        demoNode.className = "feedback ok-text";
      } else if (demo.note) {
        demoNode.textContent = "AUTO DEMO dừng · " + (demo.sent || 0) + " sự kiện · " + ((demo.viewers || []).length) + " viewer";
        demoNode.className = "feedback";
      }
    }
  }

  function selectedName() {
    return selected;
  }

  function post(action, extra) {
    var body = extra || {};
    body.action = action;
    if (!body.username && selectedName()) body.username = selectedName();
    rememberSend(action, body.username);
    return api("/host/action", { body: JSON.stringify(body) }).then(function (data) {
      var row = localRows[0];
      if (row && row.eventType === action && row.status === "SENT") {
        row.status = data.bridgeStatus || (data.ok === false ? "FAILED" : "ACCEPTED_BY_BRIDGE");
        row.evidence = "observed-bridge";
        row.eventId = data.eventId || "";
        row.summary = data.detail || action;
      }
      return data;
    });
  }

  function loadViewers() {
    var q = $("viewer-search").value || "";
    api("/host/viewers?offset=" + viewerOffset + "&q=" + encodeURIComponent(q)).then(function (data) {
      var body = $("viewer-body");
      body.innerHTML = "";
      (data.viewers || []).forEach(function (row) {
        var tr = document.createElement("tr");
        if (row.username === selected) tr.className = "pick";
        [
          row.username,
          row.userId,
          row.avatarUrl || "—",
          String(row.likes),
          row.fishingUnlocked ? "Có" : "Chưa",
          row.fishingEnabled ? "Có" : "Chưa",
          String(row.mockPoints || 0),
          missing(row.totalScore),
          missing(row.biggestFish),
          new Date(row.lastActivity * 1000).toLocaleTimeString()
        ].forEach(function (value) {
          var td = document.createElement("td");
          td.textContent = value;
          tr.appendChild(td);
        });
        tr.addEventListener("click", function () {
          selected = row.username;
          loadViewers();
          toast("Đã chọn @" + selected, "ok-text");
        });
        body.appendChild(tr);
      });
      $("viewer-page").textContent = (data.total || 0) + " người · trang " + (viewerOffset + 1);
    });
  }

  function loadBaits() {
    api("/host/baits").then(function (data) {
      $("bait-note").textContent = (data.note || "") + (data.approvedForLive ? "" : " approvedForLive = false.");
      $("bait-grid").innerHTML = "";
      (data.tiers || []).forEach(function (bait) {
        var node = document.createElement("article");
        node.className = "card bait";
        var users = bait.viewerCount ? bait.viewers.join(", ") : "Chưa có trong phiên bridge";
        node.innerHTML =
          "<h3><i class='swatch' style='background:" + bait.color + "'></i>" + bait.name + "</h3>" +
          "<p>Cấp " + bait.tier + " · mock " + bait.coins + " xu</p>" +
          "<p>Hiệu ứng " + bait.effect + "</p>" +
          "<p>Viewer bridge: " + users + "</p>";
        var button = document.createElement("button");
        button.type = "button";
        button.textContent = "Xem trước";
        button.addEventListener("click", function () {
          post("preview_bait", { tier: bait.tier }).then(function (result) { toast(result.detail, "ok-text"); });
        });
        var send = document.createElement("button");
        send.type = "button";
        send.className = "gold";
        send.textContent = "Tặng mồi";
        send.addEventListener("click", function () {
          post("gift", {
            purpose: "bait",
            giftId: bait.id,
            giftName: bait.name,
            coinValue: bait.coins,
            repeatCount: 1
          }).then(function (result) { showResult($("gift-feedback"), result); });
        });
        node.appendChild(button);
        node.appendChild(send);
        $("bait-grid").appendChild(node);
      });
    });
  }

  function loadBoard() {
    api("/host/leaderboard").then(function (data) {
      $("board-note").textContent = data.note || "";
      var body = $("board-body");
      body.innerHTML = "";
      if (!data.rows || !data.rows.length) {
        body.innerHTML = "<tr><td colspan='5'>Chưa có dữ liệu</td></tr>";
        return;
      }
      data.rows.forEach(function (row, index) {
        var tr = document.createElement("tr");
        [String(index + 1), "—", row.username, String(row.score), String(row.biggestFish)].forEach(function (value) {
          var td = document.createElement("td");
          td.textContent = value;
          tr.appendChild(td);
        });
        body.appendChild(tr);
      });
    });
  }

  function loadStruggle() {
    api("/host/struggle").then(function (data) {
      $("struggle-stats").innerHTML =
        card("Current Fisher", missing(data.currentFisher)) +
        card("Current Challenger", missing(data.currentChallenger)) +
        card("Countdown", missing(data.countdown)) +
        card("Strength", missing(data.strength)) +
        card("Fish Rarity", missing(data.fishRarity)) +
        card("Showcase State", missing(data.showcaseState));
      if (data.note) $("struggle-stats").insertAdjacentHTML("beforeend", "<p class='note'>" + data.note + "</p>");
    });
    api("/host/results").then(function (data) {
      $("result-note").textContent = data.note || "";
      $("result-wins").textContent = missing(data.wins);
      $("result-misses").textContent = missing(data.misses);
    });
  }

  function loadMetrics() {
    api("/host/metrics").then(function (data) {
      var proc = data.process || {};
      var queue = data.queue || {};
      var rss = proc.rssBytes ? Math.round(proc.rssBytes / 1048576) + " MB" : "Chưa có dữ liệu";
      var cpu = proc.cpuPercent === null || proc.cpuPercent === undefined ? (proc.cpuNote || "Đang lấy mẫu CPU") : proc.cpuPercent + " %";
      $("perf-cards").innerHTML =
        card("Python RSS", rss) +
        card("Python CPU", cpu) +
        card("Queue Length", missing(queue.depth)) +
        card("Queue Capacity", missing(queue.capacity)) +
        card("Accepted Events", missing(queue.accepted), "ok") +
        card("Dropped Events", missing(queue.dropped), "bad") +
        card("Batch Latency", queue.batchLatencyMs === null || queue.batchLatencyMs === undefined ? "Chưa có dữ liệu" : queue.batchLatencyMs + " ms") +
        card("Roblox RAM", "Chưa có telemetry") +
        card("Roblox FPS", "Chưa có telemetry");
      $("perf-note").textContent = data.roblox ? data.roblox.note : "Chưa kết nối telemetry Roblox";
    });
  }

  function loadLogs() {
    api("/host/events?limit=80").then(function (data) {
      var body = $("log-body");
      body.innerHTML = "";
      (data.events || []).forEach(function (row) {
        var tr = document.createElement("tr");
        var cells = [
          new Date(row.time * 1000).toLocaleTimeString(),
          row.username,
          row.eventType,
          row.eventId,
          row.status || row.bridgeStatus,
          row.evidence || "observed",
          row.summary
        ];
        cells.forEach(function (value) {
          var td = document.createElement("td");
          td.textContent = value || "";
          tr.appendChild(td);
        });
        body.appendChild(tr);
      });
    });
  }

  function refresh() {
    api("/host/dashboard").then(function (board) {
      paintDashboard(board);
      if (panel === "viewers") loadViewers();
      if (panel === "baits") loadBaits();
      if (panel === "leaderboard") loadBoard();
      if (panel === "fishing") loadStruggle();
      if (panel === "performance") loadMetrics();
      if (panel === "logs") loadLogs();
    }).catch(function () {
      paintDashboard(null);
      toast("PYTHON BRIDGE không trả lời.", "bad-text");
    });
    api("/v1/health").then(function (health) {
      if (!health || health.ok !== true) toast("Health bridge không ok.", "bad-text");
    }).catch(function () {
      toast("Không gọi được /v1/health.", "bad-text");
    });
  }

  function bindNav() {
    document.querySelectorAll("#nav button").forEach(function (button) {
      button.addEventListener("click", function () {
        panel = button.getAttribute("data-panel");
        document.querySelectorAll("#nav button").forEach(function (item) { item.classList.remove("on"); });
        document.querySelectorAll(".panel").forEach(function (item) { item.classList.remove("on"); });
        button.classList.add("on");
        $(panel).classList.add("on");
        $("title").textContent = titles[panel][0];
        $("subtitle").textContent = titles[panel][1];
        refresh();
      });
    });
  }

  function chip(parent, label, className, onClick) {
    var button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    if (className) button.className = className;
    button.addEventListener("click", onClick);
    parent.appendChild(button);
  }

  function boot() {
    [1, 5, 10, 20, 50, 100, 500].forEach(function (count) {
      chip($("viewer-counts"), count + " NGƯỜI", "", function () {
        post("add_viewers", { count: count }).then(function (data) {
          toast(data.detail || "", data.ok === false ? "bad-text" : "ok-text");
          loadViewers();
        });
      });
    });
    [1, 10, 100, 500].forEach(function (count) {
      chip($("like-buttons"), "+" + count + " LIKE", "gold", function () {
        post("like", { count: count }).then(function (data) { showResult($("like-feedback"), data); });
      });
    });
    ["câu", "cau", "/câu", "/cau", "/giật", "/giat", "/vitri"].forEach(function (text) {
      chip($("comment-quick"), text, "", function () {
        post("comment", { text: text }).then(function (data) { showResult($("comment-feedback"), data); });
      });
    });
    var groups = [
      ["A. Tranh cá", [
        { id: "GIFT_STEAL", name: "Rose", coins: 20, purpose: "steal" }
      ]],
      ["B. Camera Center", [
        { id: "GIFT_CAMERA", name: "Galaxy", coins: 30, purpose: "camera" }
      ]],
      ["D. Thông thường", [
        { id: "GIFT_NORMAL", name: "Heart", coins: 1, purpose: "normal" }
      ]]
    ];
    groups.forEach(function (group) {
      var wrap = document.createElement("div");
      wrap.innerHTML = "<h3>" + group[0] + "</h3>";
      var row = document.createElement("div");
      row.className = "row";
      group[1].forEach(function (gift) {
        chip(row, gift.name + " · " + gift.coins + " xu", "gold", function () {
          post("gift", {
            purpose: gift.purpose,
            giftId: gift.id,
            giftName: gift.name,
            coinValue: gift.coins,
            repeatCount: 1
          }).then(function (data) { showResult($("gift-feedback"), data); });
        });
      });
      wrap.appendChild(row);
      $("gift-groups").appendChild(wrap);
    });
    [
      ["TEST OVERRIDE · Thành công", "ForceFishSuccess"],
      ["TEST OVERRIDE · Tuột", "ForceFishEscape"],
      ["TEST OVERRIDE · Cá hiếm", "ForceFishRarity"]
    ].forEach(function (item) {
      chip($("force-row"), item[0], "", function () {
        post("force", { kind: item[1] }).then(function (data) { showResult($("fish-feedback"), data); });
      });
    });
    ["ForceFishWeight", "ForceOwnershipWinner"].forEach(function (kind) {
      chip($("force-row"), "TEST OVERRIDE · " + kind, "", function () {
        post("force", { kind: kind }).then(function (data) { showResult($("fish-feedback"), data); });
      });
    });
    [
      ["CAMERA CHÍNH", "DIORAMA"],
      ["LẤY NÉT NGƯỜI XEM", "VITRI_FOCUS"],
      ["CẬN CẢNH CÂU", "FISHING_SHOWCASE"],
      ["CAMERA TOP 10", "TOP10_LEADERBOARD"],
      ["VỀ CAMERA CHÍNH", "DIORAMA"],
      ["KẾT QUẢ CÁ", "FISH_RESULT"],
      ["CÁ TUỘT", "FISH_ESCAPE"]
    ].forEach(function (item) {
      chip($("camera-row"), item[0], "", function () {
        post("camera", { shot: item[1] }).then(function (data) { showResult($("camera-feedback"), data); });
      });
    });
    [
      ["10 viewer JOIN", "join10"],
      ["100 viewer JOIN", "join100"],
      ["500 viewer JOIN", "join500"],
      ["100 like", "likes100"],
      ["100 /giật", "giat100"],
      ["100 quà mock", "gifts100"],
      ["Event trùng", "duplicate"],
      ["Đầy hàng đợi", "overload"],
      ["Restart backend", "backend_restart"],
      ["Roblox ngắt", "roblox_disconnected"],
      ["Roblox nối lại", "roblox_reconnect"]
    ].forEach(function (item) {
      chip($("stress-row"), item[0], "", function () {
        post("stress", { kind: item[1] }).then(function (data) { showResult($("stress-feedback"), data); });
      });
    });

    $("add-viewer").addEventListener("click", function () {
      post("add_viewer", {
        username: $("viewer-name").value,
        userId: $("viewer-id").value,
        avatarUrl: $("viewer-avatar").value,
        likes: Number($("viewer-likes").value || 0),
        points: Number($("viewer-points").value || 0)
      }).then(function (data) {
        if (data.viewer) selected = data.viewer.username;
        toast(data.detail || "", data.ok === false ? "bad-text" : "ok-text");
        loadViewers();
      });
    });
    $("reset-viewer").addEventListener("click", function () {
      post("reset_viewer", { username: $("viewer-name").value || selected }).then(function (data) {
        showResult($("toast"), data);
        paintUnlock({ likes: 0 });
        loadViewers();
      });
    });
    $("remove-viewer").addEventListener("click", function () {
      post("remove_viewer", { username: $("viewer-name").value || selected }).then(function (data) {
        toast(data.detail || "", "gold-text");
        loadViewers();
      });
    });
    $("viewer-find").addEventListener("click", function () { viewerOffset = 0; loadViewers(); });
    $("viewer-prev").addEventListener("click", function () { viewerOffset = Math.max(0, viewerOffset - 50); loadViewers(); });
    $("viewer-next").addEventListener("click", function () { viewerOffset += 50; loadViewers(); });
    $("like-custom").addEventListener("click", function () {
      post("like", { count: Number($("like-count").value) }).then(function (data) { showResult($("like-feedback"), data); });
    });
    $("like-dup").addEventListener("click", function () {
      post("like", { count: Number($("like-count").value) || 1, eventId: lastEventId }).then(function (data) {
        showResult($("like-feedback"), data);
      });
    });
    $("comment-send").addEventListener("click", function () {
      post("comment", { text: $("comment-text").value }).then(function (data) { showResult($("comment-feedback"), data); });
    });
    $("gift-send").addEventListener("click", function () {
      post("gift", {
        giftId: $("gift-id").value,
        giftName: $("gift-name").value,
        coinValue: Number($("gift-coins").value),
        repeatCount: Number($("gift-repeat").value),
        purpose: $("gift-purpose").value
      }).then(function (data) { showResult($("gift-feedback"), data); });
    });
    $("fish-register").addEventListener("click", function () {
      post("register_fishing").then(function (data) { showResult($("fish-feedback"), data); });
    });
    $("fish-giat").addEventListener("click", function () {
      post("comment", { text: "/giật" }).then(function (data) { showResult($("fish-feedback"), data); });
    });
    $("fish-10").addEventListener("click", function () {
      post("pull", { count: 10 }).then(function (data) { showResult($("fish-feedback"), data); });
    });
    $("fish-100").addEventListener("click", function () {
      post("pull", { count: 100 }).then(function (data) { showResult($("fish-feedback"), data); });
    });
    $("struggle-start").addEventListener("click", function () {
      post("comment", { text: "/giật" }).then(function (data) { showResult($("fish-feedback"), data); loadStruggle(); });
    });
    $("struggle-support").addEventListener("click", function () {
      post("support_comment").then(function (data) { showResult($("fish-feedback"), data); });
    });
    $("struggle-steal").addEventListener("click", function () {
      post("gift_steal").then(function (data) { showResult($("fish-feedback"), data); });
    });
    $("struggle-win").addEventListener("click", function () {
      post("force", { kind: "ForceStruggleSuccess" }).then(function (data) { showResult($("fish-feedback"), data); });
    });
    $("struggle-lose").addEventListener("click", function () {
      post("force", { kind: "ForceStruggleFailure" }).then(function (data) { showResult($("fish-feedback"), data); });
    });
    $("board-refresh").addEventListener("click", loadBoard);
    $("board-camera").addEventListener("click", function () {
      post("preview_leaderboard_camera").then(function (data) { toast(data.detail || data.note || "", "gold-text"); });
    });
    $("board-reset").addEventListener("click", function () {
      post("reset_leaderboard").then(function (data) { toast(data.detail || "", "gold-text"); loadBoard(); });
    });
    $("stress-stop").addEventListener("click", function () {
      post("stress_stop").then(function (data) { showResult($("stress-feedback"), data); });
    });
    $("auto-demo-start").addEventListener("click", function () {
      post("auto_demo_start").then(function (data) { showResult($("auto-demo-feedback"), data); refresh(); });
    });
    $("auto-demo-stop").addEventListener("click", function () {
      post("auto_demo_stop").then(function (data) { showResult($("auto-demo-feedback"), data); refresh(); });
    });
    $("reset-session").addEventListener("click", function () {
      post("reset_session").then(function (data) {
        selected = "";
        localRows = [];
        paintUnlock({ likes: 0 });
        showResult($("stress-feedback"), data);
        refresh();
      });
    });
    $("session-start").addEventListener("click", function () {
      post("start_session").then(function (data) { showResult($("stress-feedback"), data); refresh(); });
    });
    $("session-stop").addEventListener("click", function () {
      post("stop_session").then(function (data) { showResult($("stress-feedback"), data); refresh(); });
    });
    $("send-test-event").addEventListener("click", function () {
      post("test_event").then(function (data) { showResult($("stress-feedback"), data); });
    });
    $("run-basic").addEventListener("click", function () {
      post("basic_test").then(function (data) { showResult($("stress-feedback"), data); refresh(); });
    });
    $("run-stress").addEventListener("click", function () {
      post("stress", { kind: "join100" }).then(function (data) { showResult($("stress-feedback"), data); });
    });
    $("log-clear").addEventListener("click", function () {
      localRows = [];
      post("clear_log").then(function (data) { showResult($("toast"), data); loadLogs(); });
    });
    $("log-export").addEventListener("click", function () {
      api("/host/events?limit=100").then(function (data) {
        var blob = new Blob([JSON.stringify({
          bridgeObserved: data.events || [],
          browser: localRows
        }, null, 2)], { type: "application/json" });
        var url = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = "fishinglive-host-log.json";
        link.click();
        URL.revokeObjectURL(url);
      });
    });
    bindNav();
    refresh();
    setInterval(refresh, 2000);
  }

  api("/host/session").then(function (data) {
    csrf = data.csrf || "";
    if (!csrf) {
      toast("Không lấy được phiên local.", "bad-text");
      return;
    }
    boot();
  }).catch(function () {
    toast("Mở trang bằng http://127.0.0.1:8787/host", "bad-text");
  });
})();
