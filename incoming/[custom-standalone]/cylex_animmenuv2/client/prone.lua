-- This file is a duplicate of Crouch.lua logic or meant to be separate.
-- Since the user requested fixing unreadable code and I've already fixed Crouch.lua with the same logic,
-- I will align this file with Crouch.lua to ensure consistent behavior if both are loaded.
-- If they are meant to be different, the logic in the original obfuscated file was identical to Crouch.lua.

IsProne = false
isCrouched = false
isCrawling = false
local inAction = false
local proneType = "onfront"
local lastKeyPress = 0

local function LoadAnimDict(dict)
    RequestAnimDict(dict)
    while not HasAnimDictLoaded(dict) do
        Wait(0)
    end
end

local function LoadAnimSet(set)
    RequestAnimSet(set)
    while not HasAnimSetLoaded(set) do
        Wait(0)
    end
end

local function IsAiming(ped)
    if IsPlayerFreeAiming(ped) then return true end
    if IsAimCamActive() then return true end
    if IsAimCamThirdPersonActive() then return true end
    return false
end

local function CanProne(ped)
    if IsPedOnFoot(ped) and not IsPedJumping(ped) and not IsPedFalling(ped) and not IsPedInjured(ped) and not IsPedInMeleeCombat(ped) and not IsPedRagdoll(ped) then
        return true
    end
    return false
end

local function PlayAnim(ped, dict, anim, blendIn, blendOut, duration, flag, playbackRate, lockX, lockY, lockZ)
    LoadAnimDict(dict)
    TaskPlayAnim(ped, dict, anim, blendIn or 2.0, blendOut or 2.0, duration or -1, flag or 0, playbackRate or 0.0, lockX or false, lockY or false, lockZ or false)
    RemoveAnimDict(dict)
end

local function RotateEntity(entity, angle, duration)
    local absAngle = math.abs(angle)
    local step = angle / absAngle
    local waitTime = duration / absAngle
    for i = 1, absAngle do
        Wait(waitTime)
        SetEntityHeading(entity, GetEntityHeading(entity) + step)
    end
end

local function ResetCrouch()
    local ped = PlayerPedId()
    ResetPedStrafeClipset(ped)
    ResetPedWeaponMovementClipset(ped)
    SetPedMaxMoveBlendRatio(ped, 1.0)
    SetPedCanPlayAmbientAnims(ped, true)
    
    local walkStyle = GetResourceKvpString("walkstyle")
    if walkStyle then
        LoadAnimSet(walkStyle)
        SetPedMovementClipset(ped, walkStyle, 0.6)
        RemoveAnimSet(walkStyle)
    else
        ResetPedMovementClipset(ped, 0.5)
    end
    RemoveAnimSet("move_ped_crouched")
end

local function CrouchLoop()
    CreateThread(function()
        local playerId = PlayerId()
        while isCrouched do
            local ped = PlayerPedId()
            if not CanProne(ped) then
                isCrouched = false
                break
            end
            
            if IsAiming(playerId) then
                SetPedMaxMoveBlendRatio(ped, 0.15)
            end
            
            SetPedCanPlayAmbientAnims(ped, false)
            DisableControlAction(0, 36, true) -- INPUT_DUCK
            
            if IsPedUsingActionMode(ped) then
                SetPedUsingActionMode(ped, false, -1, "DEFAULT_ACTION")
            end
            
            DisableFirstPersonCamThisFrame()
            Wait(0)
        end
        ResetCrouch()
    end)
end

local function StartCrouch()
    isCrouched = true
    LoadAnimSet("move_ped_crouched")
    local ped = PlayerPedId()
    
    if GetPedStealthMovement(ped) == 1 then
        SetPedStealthMovement(ped, false, "DEFAULT_ACTION")
        Wait(100)
    end
    
    if GetFollowPedCamViewMode() == 4 then
        SetFollowPedCamViewMode(0)
    end
    
    SetPedMovementClipset(ped, "move_ped_crouched", 0.6)
    SetPedStrafeClipset(ped, "move_ped_crouched_strafing")
    CrouchLoop()
end

function AttemptCrouch(ped)
    if CanProne(ped) then
        StartCrouch()
        return true
    end
    return false
end

function CrouchKeyPressed()
    if inAction then return end
    if isCrouched then
        isCrouched = false
        return
    end
    
    local ped = PlayerPedId()
    if Config.CrouchOverride then
        DisableControlAction(0, 36, true)
    else
        -- Check if buttons match and double press logic
        local button1 = GetControlInstructionalButton(0, 3536895674, false)
        local button2 = GetControlInstructionalButton(0, 36, false)
        
        if button1 == button2 then
            if not IsProne then
                local timer = GetGameTimer()
                if GetPedStealthMovement(ped) == 1 then
                    if (timer - lastKeyPress) < 1000 then
                         DisableControlAction(0, 36, true)
                         lastKeyPress = 0
                         AttemptCrouch(ped)
                         return
                    end
                end
                lastKeyPress = timer
                return
            end
        end
    end
    
    if AttemptCrouch(ped) then
        if IsProne then
            inAction = true
            IsProne = false
            PlayAnim(ped, "get_up@directional@transition@prone_to_knees@crawl", "front", nil, nil, 780)
            Wait(780)
            inAction = false
        end
    end
end

local function IsFastMovement(ped)
    if IsPedRunning(ped) or IsPedSprinting(ped) then
        return true
    end
    return false
end

local function PlayCrawlAnim(ped, heading, blendIn)
    local coords = GetEntityCoords(ped)
    TaskPlayAnimAdvanced(ped, "move_crawl", proneType .. "_fwd", coords.x, coords.y, coords.z, 0.0, 0.0, heading or GetEntityHeading(ped), blendIn or 2.0, 2.0, -1, 2, 1.0, false, false)
end

local function StopProne(force)
    if not force then
        inAction = true
        local ped = PlayerPedId()
        if proneType == "onfront" then
            PlayAnim(ped, "get_up@directional@transition@prone_to_knees@crawl", "front", nil, nil, 780)
            if not isCrouched then
                Wait(780)
                PlayAnim(ped, "get_up@directional@movement@from_knees@standard", "getup_l_0", nil, nil, 1300)
            end
        else
            PlayAnim(ped, "get_up@directional@transition@prone_to_seated@crawl", "back", 16.0, nil, 950)
            if not isCrouched then
                Wait(950)
                PlayAnim(ped, "get_up@directional@movement@from_seated@standard", "get_up_l_0", nil, nil, 1300)
            end
        end
    end
end

local function CrawlMovement(ped, direction, subtype)
    isCrawling = true
    TaskPlayAnim(ped, "move_crawl", proneType .. "_" .. direction, 8.0, -8.0, -1, 2, 0.0, false, false, false)
    
    local durations = {
        onfront = { fwd = 820, bwd = 990 },
        onback = { fwd = 1200, bwd = 1200 }
    }
    
    SetTimeout(durations[proneType][direction], function()
        isCrawling = false
    end)
end

local function FlipProne(ped)
    inAction = true
    local heading = GetEntityHeading(ped)
    
    if proneType == "onfront" then
        proneType = "onback"
        PlayAnim(ped, "get_up@directional_sweep@combat@pistol@front", "front_to_prone", 2.0)
        RotateEntity(ped, -18.0, 3600)
    else
        proneType = "onfront"
        PlayAnim(ped, "move_crawlprone2crawlfront", "back", 2.0, nil, -1)
        RotateEntity(ped, 12.0, 1700)
    end
    
    PlayCrawlAnim(ped, heading + 180.0)
    Wait(400)
    inAction = false
end

local function ProneLoop()
    CreateThread(function()
        Wait(400)
        local ped = PlayerPedId()
        while true do
            if not IsProne then break end
            
            ped = PlayerPedId()
            if CanProne(ped) then
                if IsEntityInWater(ped) then
                    ClearPedTasks(ped)
                    IsProne = false
                    break
                end
            else
                ClearPedTasks(ped)
                IsProne = false
                break
            end
            
            local movingFwd = IsControlPressed(0, 32)
            local movingBwd = IsControlPressed(0, 33)
            local movingLeft = IsControlPressed(0, 34)
            local movingRight = IsControlPressed(0, 35)
            
            if not isCrawling then
                if movingFwd then
                    CrawlMovement(ped, "fwd")
                elseif movingBwd then
                    CrawlMovement(ped, "bwd")
                end
            end
             
            if movingLeft then
                if isCrawling then
                    SetEntityHeading(ped, GetEntityHeading(ped) + 1.0)
                else
                    inAction = true
                    if proneType == "onfront" then
                        local coords = GetEntityCoords(ped)
                        TaskPlayAnimAdvanced(ped, "move_crawlprone2crawlfront", "left", coords.x, coords.y, coords.z, 0.0, 0.0, GetEntityHeading(ped), 2.0, 2.0, -1, 2, 0.1, false, false)
                        RotateEntity(ped, -10.0, 300)
                        Wait(700)
                    else
                        PlayAnim(ped, "get_up@directional_sweep@combat@pistol@left", "left_to_prone")
                        RotateEntity(ped, 25.0, 400)
                        PlayCrawlAnim(ped)
                        Wait(600)
                    end
                    inAction = false
                end
            elseif movingRight then
                if isCrawling then
                    SetEntityHeading(ped, GetEntityHeading(ped) - 1.0)
                else
                    inAction = true
                    if proneType == "onfront" then
                        local coords = GetEntityCoords(ped)
                        TaskPlayAnimAdvanced(ped, "move_crawlprone2crawlfront", "right", coords.x, coords.y, coords.z, 0.0, 0.0, GetEntityHeading(ped), 2.0, 2.0, -1, 2, 0.1, false, false)
                        RotateEntity(ped, 10.0, 300)
                        Wait(700)
                    else
                         PlayAnim(ped, "get_up@directional_sweep@combat@pistol@right", "right_to_prone")
                         RotateEntity(ped, -25.0, 400)
                         PlayCrawlAnim(ped)
                         Wait(600)
                    end
                    inAction = false
                end
            end

            if IsControlPressed(0, 22) then -- Jump
                FlipProne(ped)
            end

            Wait(0)
        end
        StopProne(false)
        isCrawling = false
        inAction = false
        proneType = "onfront"
        SetPedConfigFlag(ped, 48, false)
        RemoveAnimDict("move_crawl")
        RemoveAnimDict("move_crawlprone2crawlfront")
    end)
end

function CrawlKeyPressed()
    if inAction then return end
    if IsPauseMenuActive() then return end
    if IsProne then
        IsProne = false
        return
    end
    
    if IsInAnimation and IsInAnimation() then 
         EmoteCancel()
    end
    
    local isCrouchReset = false
    if isCrouched then
        isCrouched = false
        isCrouchReset = true
    end
    
    local ped = PlayerPedId()
    if not CanProne(ped) then return end
    if IsEntityInWater(ped) then return end
    
    inAction = true
    if Pointing then Pointing = false end
    
    IsProne = true
    SetPedConfigFlag(ped, 48, true)
    
    if GetPedStealthMovement(ped) == 1 then
        SetPedStealthMovement(ped, false, "DEFAULT_ACTION")
        Wait(100)
    end
    
    LoadAnimDict("move_crawl")
    LoadAnimDict("move_crawlprone2crawlfront")
    
    if IsFastMovement(ped) then
        PlayAnim(ped, "explosions", "react_blown_forwards", nil, nil, 3.0)
        Wait(1100)
    elseif isCrouchReset then
        PlayAnim(ped, "amb@world_human_sunbathe@male@front@enter", "enter", nil, nil, -1, 0.3)
        Wait(1500)
    else
        PlayAnim(ped, "amb@world_human_sunbathe@male@front@enter", "enter")
        Wait(3000)
    end
    
    if CanProne(ped) and not IsEntityInWater(ped) then
        PlayCrawlAnim(ped)
    end
    
    inAction = false
    ProneLoop()
end

-- Exports and Globals
exports("GetIsCrouched", function() return isCrouched end)
exports("GetIsProne", function() return IsProne end)
exports("GetIsCrawling", function() return isCrawling end)
