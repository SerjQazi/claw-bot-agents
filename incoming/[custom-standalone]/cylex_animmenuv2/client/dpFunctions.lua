local PlayerProps = {}

local function LoadPropDict(model)
    local hash = GetHashKey(model)
    if not IsModelValid(hash) then
        print("NO PROP FOUND! " .. model)
        return
    end
    RequestModel(hash)
    while not HasModelLoaded(hash) do
        Wait(10)
    end
end

function AddPropToPlayer(ped, propName, bone, off1, off2, off3, rot1, rot2, rot3, textureVariation)
    print("Adding Prop", propName, bone, off1, off2, off3, rot1, rot2, rot3)
    ped = ped or PlayerPedId()
    local x, y, z = table.unpack(GetEntityCoords(ped))
    
    if not HasModelLoaded(propName) then
        LoadPropDict(propName)
    end
    
    local prop = CreateObject(GetHashKey(propName), x, y, z + 0.2, true, true, true)
    if textureVariation ~= nil then
        SetObjectTextureVariation(prop, textureVariation)
    end
    
    AttachEntityToEntity(prop, ped, GetPedBoneIndex(ped, bone), (off1 or 0.0) + 0.0, (off2 or 0.0) + 0.0, (off3 or 0.0) + 0.0, (rot1 or 0.0) + 0.0, (rot2 or 0.0) + 0.0, (rot3 or 0.0) + 0.0, true, true, false, true, 1, true)
    table.insert(PlayerProps, prop)
    PlayerHasProp = true
    SetModelAsNoLongerNeeded(propName)
    return true
end

function DestroyAllProps()
    for _, prop in pairs(PlayerProps) do
        DeleteEntity(prop)
    end
    PlayerHasProp = false
    -- print("Destroyed Props")
end

function RunAnimationThread()
    if AnimationThreadStatus then return end
    AnimationThreadStatus = true
    
    CreateThread(function()
        while AnimationThreadStatus do
            local sleep = 500
            
            if not playingEmote and not PtfxPrompt then
                break
            end
            
            if playingEmote then
                sleep = 0
                if IsPedShooting(PlayerPedId()) then
                    onEmoteCancel()
                end
            end
            
            if PtfxPrompt then
                sleep = 0
                if not PtfxNotif then
                    sendNotification({text = PtfxInfo, type = "notification"}) -- Assuming signature from functions.lua
                    PtfxNotif = true
                end
                
                if IsControlPressed(0, 47) then -- G
                    PtfxStart()
                    Wait(PtfxWait)
                    if PtfxCanHold then
                        while IsControlPressed(0, 47) and playingEmote and AnimationThreadStatus do
                            Wait(5)
                        end
                    end
                    PtfxStop()
                end
            end
            
            Wait(sleep)
        end
    end)
end

-- Keybind Listener
CreateThread(function()
    local acceptBind = Config.AcceptBind or 38 -- E
    local refuseBind = Config.RefuseBind or 47 -- G
    
    local isInvitePending = false
    local inviteData = nil
    
    -- This part listens for invite events to set isInvitePending
    -- But the events are defined below. 
    -- The original code used a closure variable 'L0_1' for invite data which was shared with the event handler.
    -- I will need to use a global or upvalue.
    
    -- Re-implementing the structure with globals/upvalues properly.
    -- The event handler sets `inviteData` which is local to the file scope (or global).
    -- Original L6_1 (getAnimationInvite) sets L0_1 = A1_2 (source) and L1_1 = A0_2 (data).
    
    while true do
        Wait(0)
        -- Accessing a shared variable for pending invite
        if PendingInvite then
            if IsControlJustPressed(0, acceptBind) then
                if PendingInvite.source then
                    PlaySound(-1, "NAV", "HUD_AMMO_SHOP_SOUNDSET", 0, 0, 1)
                    TriggerServerEvent("cylex_animmenuv2:server:acceptAnimationInvite", PendingInvite.source, PendingInvite.data)
                    PendingInvite = nil
                end
            elseif IsControlJustPressed(0, refuseBind) then
                if PendingInvite.source then
                    PlaySound(-1, "NAV", "HUD_AMMO_SHOP_SOUNDSET", 0, 0, 1)
                    print("refuseemote")
                    PendingInvite = nil
                end
            end
        else
            Wait(500)
        end
    end
end)

function GetClosestPlayer()
    local players = GetPlayers()
    local closestDistance = -1
    local closestPlayer = -1
    local ped = PlayerPedId()
    local coords = GetEntityCoords(ped)
    
    for _, player in ipairs(players) do
        local targetPed = GetPlayerPed(player)
        if targetPed ~= ped then
            local targetCoords = GetEntityCoords(targetPed)
            local dist = #(coords - targetCoords)
            if closestDistance == -1 or dist < closestDistance then
                closestPlayer = player
                closestDistance = dist
            end
        end
    end
    return closestPlayer, closestDistance
end

function GetPlayers()
    local players = {}
    for i = 0, 255 do
        if NetworkIsPlayerActive(i) then
            table.insert(players, i)
        end
    end
    return players
end

RegisterNUICallback("sendAnimationInvite", function(data, cb)
    print("sendAnimationInvite", json.encode(data))
    local closestPlayer, closestDistance = GetClosestPlayer()
    
    if closestPlayer == -1 or closestDistance == -1 or closestDistance > 3.0 then
        sendNotification({
            timeout = 5,
            title = Lang("notification_error"),
            text = Lang("no_player_nearby"),
            type = "notification"
        })
        cb("ok")
        return
    end
    
    TriggerServerEvent("cylex_animmenuv2:server:sendAnimationInvite", GetPlayerServerId(closestPlayer), data)
    cb("ok")
end)

local function GetAnimationData(id)
    for _, anim in pairs(Config.AllAnimations) do
        if anim.id == id then
            return anim
        end
    end
    return false
end

-- PendingInvite Global
PendingInvite = nil

RegisterNetEvent("cylex_animmenuv2:client:getAnimationInvite", function(data, sourcePlayer)
    PlaySound(-1, "NAV", "HUD_AMMO_SHOP_SOUNDSET", 0, 0, 1)
    
    PendingInvite = {
        source = sourcePlayer,
        data = data
    }
    
    SetTimeout(5500, function()
        PendingInvite = nil
    end)
    
    print("Received Invite", json.encode(data))
    
    local animData = data
    if data.targetAnim then
        animData = GetAnimationData(data.targetAnim) or data
    end
    
    print("Invite Anim Data", json.encode(animData))
    
    sendNotification({
        timeout = 5,
        title = Lang("new_invite"),
        text = string.format(Lang("invited_animation"), animData.label),
        description = Lang("invite_question"),
        type = "invite",
        anim = data
    })
end)

RegisterNetEvent("cylex_animmenuv2:client:playSyncedAnim", function(data, sourcePlayer)
    onEmoteCancel()
    Wait(300)
    
    local syncData = GetAnimationData(data.id)
    if not syncData then return end
    
    local settings = syncData.animSettings
    if settings and settings.Attachto then
        -- This logic seems to check if the target also has attachment settings or if we need to swap roles?
        -- The original code logic is a bit convoluted around lbl_132 and recursive checks.
        -- Basically if Attached, we might need to find the OTHER animation to play or position?
        
        local targetAnim = GetAnimationData(syncData.targetAnim or (settings.Attachto.id)) 
        -- If we attach to something, we play the animation that responds to it? 
        
        -- Simplified logic from original:
        -- It calculates offset and attaches entity to the source player.
        local playerFromServer = GetPlayerFromServerId(sourcePlayer)
        if not playerFromServer or playerFromServer == 0 then
            print("cylex_animmenuv2:client:playSyncedAnim: Player not found!")
            return
        end
        
        local targetPed = GetPlayerPed(playerFromServer)
        if not DoesEntityExist(targetPed) then
            print("cylex_animmenuv2:client:playSyncedAnim: Player PED not found!")
            return
        end
        
        local bone = settings.bone or -1
        local x = settings.xPos or 0.0
        local y = settings.yPos or 0.0
        local z = settings.zPos or 0.0
        local xRot = settings.xRot or 0.0
        local yRot = settings.yRot or 0.0
        local zRot = settings.zRot or 0.0
        
        AttachEntityToEntity(PlayerPedId(), targetPed, GetPedBoneIndex(targetPed, bone), x, y, z, xRot, yRot, zRot, false, false, false, true, 1, true)
    end
    
    onAnimTriggered(syncData)
end)

RegisterNetEvent("cylex_animmenuv2:client:playSyncedAnimSource", function(data, sourcePlayer)
    print("cylex_animmenuv2:client:playSyncedAnimSource", json.encode(data))
    
    local playerFromServer = GetPlayerFromServerId(sourcePlayer)
    if not playerFromServer or playerFromServer == 0 then
        print("cylex_animmenuv2:client:playSyncedAnimSource: Player not found!")
        return
    end
    
    local targetPed = GetPlayerPed(playerFromServer)
    if not DoesEntityExist(targetPed) then
        print("cylex_animmenuv2:client:playSyncedAnimSource: Player PED not found!")
        return
    end
    
    -- Default offsets
    -- The original code seemed to have defaults L5_2=1.0 etc logic, but it was unused mostly or initialized.
    
    local animData = GetAnimationData(data.id)
    if not animData then return end
    
    local settings = animData.animSettings
    if settings then
        -- SyncOffset logic which sets some vars but they are not used in AttachEntityToEntity directly below?
        -- Wait, if Attachto is present (L11_2), it attaches targetPed to US?
        -- L18_2 = AttachEntityToEntity, L19_2 = L2_2 (PlayerPedId), L20_2 = L4_2 (targetPed?? No L4_2 is targetPed)
        -- Actually argument order: AttachEntityToEntity(entity1, entity2, ...)
        
        if settings.Attachto then
             local bone = settings.bone or -1
             local x = settings.xPos or 0.0
             local y = settings.yPos or 0.0
             local z = settings.zPos or 0.0
             local xRot = settings.xRot or 0.0
             local yRot = settings.yRot or 0.0
             local zRot = settings.zRot or 0.0
             
             -- Attaching TARGET to LOCAL PLAYER?
             -- Original: AttachEntityToEntity(targetPed, PlayerPedId(), ...)
             AttachEntityToEntity(targetPed, PlayerPedId(), GetPedBoneIndex(PlayerPedId(), bone), x, y, z, xRot, yRot, zRot, false, false, false, true, 1, true)
        end
    end
    
    onAnimTriggered(animData)
end)
