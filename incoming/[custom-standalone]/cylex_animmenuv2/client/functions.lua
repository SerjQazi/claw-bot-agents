local currentAnim = nil
local playingEmote = false
local AnimationDuration = -1
local ChosenAnimation = ""
local ChosenDict = ""
local MostRecentChosenAnimation = ""
local MostRecentChosenDict = ""
local MovementType = 0
local PlayerGender = "male"
local PlayerHasProp = false
local PlayerParticles = {}
local SecondPropEmote = false
local PtfxNotif = false
local PtfxPrompt = false
local PtfxWait = 500
local PtfxCanHold = false
local PtfxNoProp = false
local AnimationThreadStatus = false

-- Bone/Control Table logic from original L0_1 (lines 174..) seems to be `GameEnums` or similar.
-- But it was just a numeric array L0_1[1] to L0_1[39].
-- Looking at usage in L1_1 (onEmoteCancel) -> L3_2 = L0_1; for L5_2=1,#L3_2... GetClosestObjectOfType(..., L0_1[L5_2])
-- So it is a list of Object Hashes to clean up?
local CleanupModels = {
    1603835013, -598185919, -1412276716, -1254540419, 692857360, 1027109416, -1555713785, -1630172026, -960996301,
    -708789241, 2017086435, 974883178, -245386275, -533655168, -1109340972, -801803927, 591916419, -1425058769,
    -461945070, -693032058, 2010247122, 1151364435, -1934174148, 176137803, -2013814998, -1910604593, 1338703913,
    -334989242, -839348691, 679927467, -66965919, -110986183, -1199910959, -113902346, -127739306, 1360563376,
    2052737670, -1010290664, -580196246
}

function sendNotification(data)
    local text = data.text or ""
    local title = data.title or "Notification"
    local type = data.type or "notification"
    local timeout = data.timeout or 5
    
    SendNUIMessage({
        action = "notification",
        data = {
            type = type,
            title = title,
            text = text,
            description = data.description,
            anim = data.anim,
            timeout = timeout
        }
    })
end

function loadAnimSet(set)
    if HasAnimSetLoaded(set) then return true end
    local timeout = GetGameTimer() + 5000
    RequestAnimSet(set)
    while not HasAnimSetLoaded(set) do
        if GetGameTimer() > timeout then
            print("Could not load walk style: " .. set)
            return false
        end
        Wait(0)
    end
    return true
end

function loadAnim(dict)
    if not DoesAnimDictExist(dict) then
        print("Anim not found in streams: " .. dict)
        return false
    end
    if HasAnimDictLoaded(dict) then return true end
    
    local timeout = GetGameTimer() + 5000
    RequestAnimDict(dict)
    while not HasAnimDictLoaded(dict) do
        if GetGameTimer() > timeout then
            print("Could not load animation dictionary: " .. dict)
            return false
        end
        Wait(0)
    end
    return true
end

function clearProps(ped)
    DetachEntity(ped, true, false)
    TriggerEvent("cylex_animmenuv2:propattach:destroyProp", ped)
    TriggerEvent("cylex_animmenuv2:propattach:destroyProp2", ped)
    DestroyAllProps()
end

function onEmoteCancel()
    local ped = PlayerPedId()
    if not playingEmote then
        print("No emote playing")
        return
    end
    
    PtfxNotif = false
    PtfxPrompt = false
    Pointing = false
    
    if LocalPlayer.state.ptfx then
        PtfxStop()
    end
    
    if playingEmote.scenario then
        ClearPedTasks(ped)
        TaskStartScenarioInPlace(ped, playingEmote.scenario, 0, true)
        Wait(0)
        if not IsPedFalling(ped) then
            ClearPedTasksImmediately(ped)
        end
        ClearPedTasks(ped)
        DetachEntity(ped, true, false)
        
        -- Cleanup Props from World
        local coords = GetEntityCoords(ped)
        for _, model in ipairs(CleanupModels) do
            local obj = GetClosestObjectOfType(coords.x, coords.y, coords.z, 1.0, model, false, true, true)
            if DoesEntityExist(obj) then
                SetEntityAsMissionEntity(obj, false, false)
                DeleteObject(obj)
            end
        end
    end
    
    ClearPedTasks(ped)
    clearProps(ped)
    TriggerEvent("turnoffsitting")
    TriggerEvent("animation:gotCanceled")
    TriggerEvent("cylex_animmenuv2:client:onEmoteCancel", playingEmote)
    
    playingEmote = false
    AnimationThreadStatus = false
    FreezeEntityPosition(ped, false)
    
    if lastPosCoords and Config.AnimPos and Config.AnimPos.TeleportBackOnCancel then
        SetEntityCoords(ped, lastPosCoords.x, lastPosCoords.y, lastPosCoords.z - 1.0)
        lastPosCoords = nil
    end
    
    ClearPedTasksImmediately(ped)
end

function onAnimTriggered(data, targetPed)
    if not data then return end
    if data.disabled then
        print("This emote is disabled for now", data)
        return
    end
    
    local ped = targetPed or PlayerPedId()
    
    if data.category == "walks" then return onWalk(data) end
    if data.category == "expressions" then return onExpression(data) end
    
    if not Config.MultipleAnim and playingEmote then
        print("multiple emotes is disabled")
        return
    end
    
    if data.scenario then
        ClearPedTasks(ped)
        TaskStartScenarioInPlace(ped, data.scenario, 0, true)
        playingEmote = data
        return true
    end
    
    if data.dict and data.dict ~= "" then
        if not loadAnim(data.dict) then return false end
        
        local movementFlag = 0
        local duration = data.duration or -1
        local attachWait = 0
        
        if data.animSettings then
            if data.animSettings.EmoteLoop then
                movementFlag = 1
                if data.animSettings.EmoteMoving then movementFlag = 51 end
            else
                if data.animSettings.EmoteMoving then
                    movementFlag = 51
                elseif not data.animSettings.EmoteMoving then
                    movementFlag = 0
                elseif data.animSettings.EmoteStuck then
                    movementFlag = 50
                end
            end
            
            if data.animSettings.FreezeLastFrame then movementFlag = 2 end
            
            if data.animSettings.EmoteDuration then
                duration = data.animSettings.EmoteDuration
                attachWait = duration
            end
            
            if data.animSettings.PtfxAsset then
                PtfxAsset = data.animSettings.PtfxAsset
                PtfxName = data.animSettings.PtfxName
                PtfxNoProp = data.animSettings.PtfxNoProp or false
                local placement = data.animSettings.PtfxPlacement
                Ptfx1, Ptfx2, Ptfx3, Ptfx4, Ptfx5, Ptfx6, PtfxScale = table.unpack(placement)
                PtfxBone = data.animSettings.PtfxBone
                PtfxColor = data.animSettings.PtfxColor
                PtfxInfo = data.animSettings.PtfxInfo
                PtfxWait = data.animSettings.PtfxWait
                PtfxCanHold = data.animSettings.PtfxCanHold
                PtfxNotif = false
                PtfxPrompt = true
                
                TriggerServerEvent("cylex_animmenuv2:ptfx:sync", PtfxAsset, PtfxName, vector3(Ptfx1, Ptfx2, Ptfx3), vector3(Ptfx4, Ptfx5, Ptfx6), PtfxBone, PtfxScale, PtfxColor)
            else
                PtfxPrompt = false
            end
        end
        
        if data.flag then movementFlag = data.flag end
        if data.category == "dances" then movementFlag = 1 end
        
        TaskPlayAnim(ped, data.dict, data.anim, 3.0, 3.0, duration, movementFlag, 0, false, false, false)
        RunAnimationThread()
        
        if data.prop then
            TriggerEvent("cylex_animmenuv2:propattach:attachItem", data.prop)
        end
        
        if data.animSettings and data.animSettings.Prop then
            local PropName = data.animSettings.Prop
            local PropBone = data.animSettings.PropBone
            local PropPl1, PropPl2, PropPl3, PropPl4, PropPl5, PropPl6 = table.unpack(data.animSettings.PropPlacement)
            
            if data.animSettings.SecondProp then
                SecondPropName = data.animSettings.SecondProp
                SecondPropBone = data.animSettings.SecondPropBone
                SecondPropPl1, SecondPropPl2, SecondPropPl3, SecondPropPl4, SecondPropPl5, SecondPropPl6 = table.unpack(data.animSettings.SecondPropPlacement)
                SecondPropEmote = true
            else
                SecondPropEmote = false
            end
            
            Wait(attachWait)
            AddPropToPlayer(ped, PropName, PropBone, PropPl1, PropPl2, PropPl3, PropPl4, PropPl5, PropPl6, data.animSettings.textureVariation) -- textureVariation assumed
            
            if SecondPropEmote then
                AddPropToPlayer(ped, SecondPropName, SecondPropBone, SecondPropPl1, SecondPropPl2, SecondPropPl3, SecondPropPl4, SecondPropPl5, SecondPropPl6, data.animSettings.textureVariation)
            end
            
            if data.animSettings.PtfxAsset and not PtfxNoProp then
                -- Note: 'prop' variable here refers to the last created prop from AddPropToPlayer? 
                -- AddPropToPlayer pushes to PlayerProps. Last one is #PlayerProps.
                local lastProp = PlayerProps[#PlayerProps]
                if lastProp then
                    TriggerServerEvent("cylex_animmenuv2:ptfx:syncProp", ObjToNet(lastProp))
                end
            end
        end
        
        if data.prop2 then
             TriggerEvent("cylex_animmenuv2:propattach:attachItem2", data.prop2)
        end
        
        playingEmote = data
        return true
    end
    
    if data.event then
        TriggerEvent(data.event)
        return true
    end
end

function onWalk(data)
    local ped = PlayerPedId()
    if data.value == "default" then
        onWalkCancel()
        return
    end
    
    if loadAnimSet(data.value) then
        SetPedMovementClipset(ped, data.value, 0.5)
        if Config.PersistentWalkStyle then
            SetResourceKvp("cylex_animmenuv2:walkstyle", data.value)
        end
    end
end

function onWalkCancel()
    local ped = PlayerPedId()
    ResetPedMovementClipset(ped, 0.0)
    TriggerEvent("cylex_animmenuv2:client:onWalkSet", "default")
    if Config.PersistentWalkStyle then
        DeleteResourceKvp("cylex_animmenuv2:walkstyle")
    end
end

function onExpression(data)
    local ped = PlayerPedId()
    if data.value == "default" then
        ClearFacialIdleAnimOverride(ped)
        return
    end
    SetFacialIdleAnimOverride(ped, data.value, 0)
end

function onExpressionCancel()
    local ped = PlayerPedId()
    ClearFacialIdleAnimOverride(ped)
end
