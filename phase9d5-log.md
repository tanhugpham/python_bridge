# PHASE 9D.5 log

Place 131643984728821 (Hung1). Universe 10776877200. Studio Play, mock APIs. HTTP off. BridgeEnabled false. BridgeMode mock. No publish.

## Root cause

1. Camera followed Host because CameraController returned before Scriptable when `#points < 8`, even though the 45–50° FrameDistance diorama does not need dock corners. Default Custom camera then followed the Host Humanoid.
2. HostUserId was empty on this place. HostSpectatorConfig.HostUserIds stayed {}. Host hide only runs when FishingLiveHost is true.
3. NPC DESPAWNING: ViewerManager.DetachWalker called MovementScheduler.remove, which writes FLState DESPAWNING. CharacterPool.transfer reused the same rig. prepare() did not clear FLState. seat() did not set FISHING.

## Fix

- CameraController applies Scriptable + FrameDistance diorama even with fewer than 8 dock points, and clears CameraSubject.
- ReplicatedStorage.FishingLive HostUserId = 1126300325. HostSpectator reads that attribute. HostUserIds stays empty.
- DetachWalker stops animation, anchors the rig, and clears DESPAWNING on fishing handoff.
- Successful seat sets FLState FISHING. Failed seat and releaseSeat clear it.

## Test

Play as UserId 1126300325.

- CameraType Scriptable, pitch 47, FOV 40, phase DIORAMA, CFrame about (-22, 109, 123). Host parked (220, 20, 220), 19/19 parts hidden.
- 500 likes unlocked. /cau seated live_alpha on L07, FLState FISHING.
- /giat queued then FISHING_SHOWCASE; rig stayed FISHING.
- Bravo 20-coin gift steal=true; duplicate eventId rejected. Bravo dock R07. Both FISHING.
- /vitri queued, POSITION_FOCUS name alpha, then DIORAMA. Second /vitri cooldown.
- 10 Reset/Join/500 likes/cau/giat cycles: all FISHING, no badState, fisherCount 1 each cycle, peak NPC 1, peak workspace Humanoids 2, end NPC 0.
- MCP screen_capture stayed black (known capture limit). Visual 10-fisher/house/TOP10 frame not confirmed from image.

## Safety

HttpEnabled false after Stop. BridgeEnabled false. BridgeMode mock. Secret FISHINGLIVE_BRIDGE_READ_KEY missing-or-unreadable in Play. Not copied from the old place. Not published.
