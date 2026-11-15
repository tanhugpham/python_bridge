# PHASE 9D.4 log

Place 94332263216492. Studio play, direct mock calls. HTTP off. BridgeEnabled false. BridgeMode mock. No publish.

## Root cause

The only writer of FLState DESPAWNING is MovementScheduler.remove. Fishing registration seats through FishingParticipantManager.takeRig, which calls ViewerManager.DetachWalker. DetachWalker calls remove, then CharacterPool.transfer reuses that same rig. prepare sets FLGroup fishing and does not clear FLState. releaseWalker clears the attribute. DetachWalker did not. A second /cau does not reseat because fishingEnabled is already true. Inactivity is off by default (180s) and was not the cause.

## Fix

DetachWalker now stops the walker animation, anchors the rig, and clears DESPAWNING because the rig is handed off, not destroyed. A successful seat sets FLState FISHING. releaseSeat and a failed seat clear FLState. Cap stays 20. No permanent humanoid per viewer.

## Host

Allowlist stays empty. No UserId was invented. Owner entry is attribute HostUserId on ReplicatedStorage.FishingLiveHost (number or digits). Empty grants nobody. When set, the existing local hide and spectator park apply only to that UserId.

## Test

Mock path: FLViewerApi.JoinViewer, FLFishApi likes /cau /giat /vitri / gift. Not HTTP.

- alpha joined walking, 500 likes unlocked, /cau visible on dock L04, FLState FISHING, group fishing. Walker was not left in Ambient.
- 30s hold: FISHING on all 7 samples.
- bravo dock L05. Docks differed. No duplicate viewer rig.
- /giat before a showcase queued and started FISHING_SHOWCASE. Rig stayed FISHING through the shot.
- Result phase seen, then CameraPhase DIORAMA. Rig still FISHING until release.
- During struggle, 20-coin gift from bravo returned steal. The same eventId was duplicate. Bravo /giat was support. Alpha /giat during the shot was self support, not a second pull.
- /vitri queued, PositionName alpha, PositionFocus within 8 studs of the rig, no extra humanoid. Second /vitri returned cooldown. Phase returned to DIORAMA.
- Viewport camera stayed Custom at FOV 70 during POSITION_FOCUS. The diorama solver returns when it has fewer than 8 dock points, before it applies the shot. Pitch, yaw, FOV, and frame distance were not edited.
- Register, spawn, showcase, result, release repeated 10 times. All 10 released to 0 NPC humanoids.
- Peak NPC humanoids 2. Peak workspace humanoids 3, including the Studio player. Pool held returned to 0.
- Memory about 1605 MB to 1628 MB across the run.
- HostConfigured false. HostUserId empty. Studio player FishingLiveHost false. Local parts hidden 0 of 19.
- Existing ViewerError remains: waypoints-canh 2 gap. Walkers still spawned.

## Safety

HttpEnabled false after Stop. BridgeEnabled false. BridgeMode mock. BridgeState during play was disabled.
