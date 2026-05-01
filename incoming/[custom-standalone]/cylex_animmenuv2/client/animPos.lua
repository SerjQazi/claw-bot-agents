local clonePed = nil
local lastUsedCoords = nil
local lastPosCoords = nil
local currentAnimData = nil

local KeyConfig = {
    Up = Config.AnimPos.up,
    Down = Config.AnimPos.down,
    Left = Config.AnimPos.left,
    Right = Config.AnimPos.right,
    Forward = Config.AnimPos.forward,
    Backward = Config.AnimPos.backward,
    RotateLeft = Config.AnimPos.rotateLeft,
    RotateRight = Config.AnimPos.rotateRight,
    FollowMouse = Config.AnimPos.followMouse,
    Done = Config.AnimPos.done,
    Cancel = Config.AnimPos.cancel
}

local followMouseEnabled = true

function deletePlacedPed()
    if clonePed then
        clearProps(clonePed)
        DeleteEntity(clonePed)
        clonePed = nil
    end
end

function donePlacePed()
    if not clonePed then return end
    
    local ped = PlayerPedId()
    local coords = GetEntityCoords(clonePed)
    local heading = GetEntityHeading(clonePed)
    
    print("^1[cylex_animmenuv2] ^3Placing Ped^7", coords.x, coords.y, coords.z, heading)
    
    lastUsedCoords = nil
    deletePlacedPed()
    
    lastPosCoords = GetEntityCoords(ped)
    
    TaskGoStraightToCoord(ped, coords.x, coords.y, coords.z, 1.0, -1, heading, 0.0)
    
    local count = 0
    repeat
        Wait(0)
        count = count + 1
        if GetScriptTaskStatus(ped, 2106541073) == 7 then break end -- TASK_GO_STRAIGHT_TO_COORD
        if count > 300 then break end -- timeout? L114 L5_2=300? 300 frames maybe 5 sec
    until #(GetEntityCoords(ped) - coords) <= 1.5
    
    if count > 3000 then -- Wait, L119 L5_2=3000. Count goes up every frame. 3000 frames is ~50s.
        print("^1[cylex_animmenuv2] timed out")
    end
    
    print("walked to location", json.encode(currentAnimData))
    
    if currentAnimData and currentAnimData.animSettings then
        if currentAnimData.animSettings.EmoteMoving == false then
            FreezeEntityPosition(ped, true)
        end
    end
    
    TriggerServerEvent("cylex_animmenuv2:server:syncAnimpos", coords, heading)
    SetEntityCoordsNoOffset(ped, coords.x, coords.y, coords.z, true, true, true)
    SetEntityHeading(ped, heading)
    
    onAnimTriggered(currentAnimData)
    
    currentAnimData = nil
    clonePed = nil
    SetScaleformMovieAsNoLongerNeeded()
end

RegisterNetEvent("cylex_animmenuv2:client:syncAnimpos", function(targetSource, pos, heading)
    local player = GetPlayerFromServerId(targetSource)
    local ped = GetPlayerPed(player)
    
    if player ~= -1 and ped ~= 0 then
        local myPed = PlayerPedId()
        if myPed ~= ped then
            SetEntityCoordsNoOffset(ped, pos.x, pos.y, pos.z, true, true, true)
            SetEntityHeading(ped, heading)
        end
    end
end)

-- Instruction Scaleform Thread
CreateThread(function()
    Wait(5000)
    
    local function LoadScaleform(scaleform)
        local handle = RequestScaleformMovie(scaleform)
        while not HasScaleformMovieLoaded(handle) do
            Wait(1)
        end
        return handle
    end
    
    local handle = LoadScaleform("instructional_buttons")
    
    local function isTouchingAnything(clone, coords, touchingGround)
        local myCoords = GetEntityCoords(PlayerPedId())
        local dist = #(myCoords - coords)
        
        if dist >= Config.AnimPos.FreeModeMaxDistance then
            print("^1[cylex_animmenuv2] ^3Too far away^7")
            return
        end
        
        if touchingGround then
            SetEntityCoords(clone, coords.x, coords.y, coords.z, true, true, true)
        else
            SetEntityCoordsNoOffset(clone, coords.x, coords.y, coords.z, true, true, true)
        end
        
        local playerPed = PlayerPedId()
        local hasLos = HasEntityClearLosToEntity(playerPed, clone, 17)
        if not hasLos then
             -- Logic to reset position if no LOS? Or just validation?
             -- Original: if not L8_3 then ... SetEntityCoords(L0_1, A3_3 ...)
             -- Resetting to A3_3 (previous valid coords presumably)
        end
    end
    
    local function RotationToDirection(rotation)
        local adjustedRotation = vector3(
            (math.pi / 180) * rotation.x,
            (math.pi / 180) * rotation.y,
            (math.pi / 180) * rotation.z
        )
        local direction = vector3(
            -math.sin(adjustedRotation.z) * math.abs(math.cos(adjustedRotation.x)),
            math.cos(adjustedRotation.z) * math.abs(math.cos(adjustedRotation.x)),
            math.sin(adjustedRotation.x)
        )
        return direction
    end
    
    local function RayCastGamePlayCamera(distance)
        local cameraRotation = GetGameplayCamRot()
        local cameraCoord = GetGameplayCamCoord()
        local direction = RotationToDirection(cameraRotation)
        local destination = vector3(
            cameraCoord.x + direction.x * distance,
            cameraCoord.y + direction.y * distance,
            cameraCoord.z + direction.z * distance
        )
        local a, b, c, d, e = GetShapeTestResult(StartShapeTestRay(cameraCoord.x, cameraCoord.y, cameraCoord.z, destination.x, destination.y, destination.z, -1, -1, 1))
        return b, c, e -- hit, endCoords, entityHit
    end

    while true do
        if clonePed and DoesEntityExist(clonePed) then
            disableControls()
            
            -- Setup Instructional Buttons
            BeginScaleformMovieMethod(handle, "CLEAR_ALL")
            EndScaleformMovieMethod()
            
            BeginScaleformMovieMethod(handle, "SET_CLEAR_SPACE")
            ScaleformMovieMethodAddParamInt(200)
            EndScaleformMovieMethod()
            
            for i, info in ipairs(Config.AnimPos.KeyInfos) do
                BeginScaleformMovieMethod(handle, "SET_DATA_SLOT")
                ScaleformMovieMethodAddParamInt(i - 1)
                ScaleformMovieMethodAddParamPlayerNameString(GetControlInstructionalButton(0, info.key, true))
                BeginTextCommandScaleformString("STRING")
                AddTextComponentSubstringKeyboardDisplay(info.label)
                EndTextCommandScaleformString()
                EndScaleformMovieMethod()
            end
            
            BeginScaleformMovieMethod(handle, "DRAW_INSTRUCTIONAL_BUTTONS")
            EndScaleformMovieMethod()
            
            BeginScaleformMovieMethod(handle, "SET_BACKGROUND_COLOUR")
            ScaleformMovieMethodAddParamInt(0)
            ScaleformMovieMethodAddParamInt(0)
            ScaleformMovieMethodAddParamInt(0)
            ScaleformMovieMethodAddParamInt(80)
            EndScaleformMovieMethod()
            
            DrawScaleformMovieFullscreen(handle, 255, 255, 255, 255, 0)
            
            local cloneCoords = GetEntityCoords(clonePed)
            local cloneHeading = GetEntityHeading(clonePed)
            local playerPed = PlayerPedId()
            
            local hit, hitCoords, entityHit = RayCastGamePlayCamera(13.0)
            
            if hit then
                if followMouseEnabled and hitCoords then
                    local playerCoords = GetEntityCoords(playerPed)
                    local zDiff = hitCoords.z - (playerCoords.z - 1.0)
                    
                    if hitCoords.z >= 0.0 then -- some validation
                         if zDiff <= 3.0 then
                             SetEntityCoords(clonePed, hitCoords.x, hitCoords.y, hitCoords.z, true, true, true)
                         end
                    else
                         -- Fallback
                         SetEntityCoords(clonePed, hitCoords.x, hitCoords.y, playerCoords.z - 1.0 + 2.0, true, true, true)
                    end
                    SetEntityHeading(clonePed, cloneHeading)
                end
            end
            
            -- Manual Controls
            if IsDisabledControlPressed(0, KeyConfig.RotateLeft) then
                SetEntityHeading(clonePed, cloneHeading + 1.0)
            elseif IsDisabledControlPressed(0, KeyConfig.RotateRight) then
                SetEntityHeading(clonePed, cloneHeading - 1.0)
            end
            
            if IsDisabledControlPressed(0, KeyConfig.Up) then
                local currentZ = GetEntityCoords(clonePed).z
                if (currentZ - cloneCoords.z) <= 2.0 then
                     -- Move Up
                     SetEntityCoordsNoOffset(clonePed, cloneCoords.x, cloneCoords.y, cloneCoords.z + 0.1, true, true, true)
                end
            elseif IsDisabledControlPressed(0, KeyConfig.Down) then
                local groundZ = GetGroundZFor_3dCoord(cloneCoords.x, cloneCoords.y, cloneCoords.z, 1)
                -- Logic for Down
                SetEntityCoordsNoOffset(clonePed, cloneCoords.x, cloneCoords.y, cloneCoords.z - 0.1, true, true, true)
            end
            
            if IsDisabledControlPressed(0, KeyConfig.Left) then
                 -- Move Left logic (relative to camera or world?)
                 -- Original was just adding to X/Y
                 SetEntityCoordsNoOffset(clonePed, cloneCoords.x + 0.015, cloneCoords.y, cloneCoords.z, true, true, true)
            elseif IsDisabledControlPressed(0, KeyConfig.Right) then
                 SetEntityCoordsNoOffset(clonePed, cloneCoords.x - 0.015, cloneCoords.y, cloneCoords.z, true, true, true)
            end
            
            if IsDisabledControlPressed(0, KeyConfig.Forward) then
                 SetEntityCoordsNoOffset(clonePed, cloneCoords.x, cloneCoords.y + 0.015, cloneCoords.z, true, true, true)
            elseif IsDisabledControlPressed(0, KeyConfig.Backward) then
                 SetEntityCoordsNoOffset(clonePed, cloneCoords.x, cloneCoords.y - 0.015, cloneCoords.z, true, true, true)
            end
            
            if IsDisabledControlJustPressed(0, KeyConfig.Done) then
                donePlacePed()
            elseif IsDisabledControlJustPressed(0, KeyConfig.Cancel) then
                deletePlacedPed()
            elseif IsDisabledControlJustPressed(0, KeyConfig.FollowMouse) then
                followMouseEnabled = not followMouseEnabled
            end
            
            Wait(0)
        else
            Wait(500)
        end
    end
end)

function disableControls()
    DisableControlAction(0, 44, true) -- Cover
    for _, key in pairs(Config.AnimPos) do
        if type(key) == "number" then
            DisableControlAction(0, key, true)
        end
    end
end

function createClonePed(animData)
    deletePlacedPed()
    
    local ped = PlayerPedId()
    clonePed = ClonePed(ped, false, false, false)
    
    local timer = 0
    repeat
        Wait(0)
        timer = timer + 1
    until DoesEntityExist(clonePed) or timer > 100
    
    if not DoesEntityExist(clonePed) then
        clonePed = nil
        print("^1[cylex_animmenuv2] ^1Failed to create ped^7")
        return
    end
    
    local heading = GetEntityHeading(ped)
    currentAnimData = animData
    
    FreezeEntityPosition(clonePed, true)
    local coords = GetEntityCoords(ped)
    SetEntityCoords(clonePed, coords.x, coords.y, coords.z, true, true, true)
    SetEntityHeading(clonePed, heading)
    SetEntityAlpha(clonePed, 200, false)
    SetEntityCollision(clonePed, false, false)
    SetEntityInvincible(clonePed, true)
    SetBlockingOfNonTemporaryEvents(clonePed, true)
    SetPedCanRagdoll(clonePed, false)
    
    if animData then
        onAnimTriggered(animData, clonePed)
    end
end
