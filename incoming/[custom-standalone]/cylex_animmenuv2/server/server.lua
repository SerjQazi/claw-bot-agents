local function debugPrint(...)
    if Config and Config.Debug then
        print(...)
    end
end
-- Keep original print or use a different logger if needed, but original used local override
local serverPrint = print

RegisterNetEvent("cylex_animmenuv2:ptfx:sync", function(asset, name, offset, rot, bone, scale, color)
    if type(asset) ~= "string" or type(name) ~= "string" or type(offset) ~= "vector3" or type(rot) ~= "vector3" then
        debugPrint("[cylex_animmenuv2] ptfx:sync: invalid arguments for source:", source)
        return
    end

    local PlayerState = Player(source).state
    PlayerState:set("ptfxAsset", asset, true)
    PlayerState:set("ptfxName", name, true)
    PlayerState:set("ptfxOffset", offset, true)
    PlayerState:set("ptfxRot", rot, true)
    PlayerState:set("ptfxBone", bone, true)
    PlayerState:set("ptfxScale", scale, true)
    PlayerState:set("ptfxColor", color, true)
    PlayerState:set("ptfxPropNet", false, true)
    PlayerState:set("ptfx", false, true)
end)

RegisterNetEvent("cylex_animmenuv2:ptfx:syncProp", function(netId)
    local PlayerState = Player(source).state
    if netId then
        local count = 0
        while count <= 100 do
            if DoesEntityExist(NetworkGetEntityFromNetworkId(netId)) then
                break
            end
            Wait(10)
            count = count + 1
        end

        if count < 100 then
            PlayerState:set("ptfxPropNet", netId, true)
            return
        end
    end
    PlayerState:set("ptfxPropNet", false, true)
end)

local AnimationListFile = "animations/AnimationList.json"

function createJson()
    local content = LoadResourceFile(GetCurrentResourceName(), AnimationListFile)
    if content == nil or content == "" then
        SaveResourceFile(GetCurrentResourceName(), AnimationListFile, json.encode({}), -1)
        return {}
    end
    return json.decode(content)
end

local animationInvites = {}

RegisterNetEvent("cylex_animmenuv2:server:sendAnimationInvite", function(targetId, data)
    local src = source
    local target = tonumber(targetId)

    if not target then return end
    if not GetPlayerName(target) then return end
    if not data.animation then return end

    local ped = GetPlayerPed(target)
    local srcPed = GetPlayerPed(src)
    local targetCoords = GetEntityCoords(ped)
    local srcCoords = GetEntityCoords(srcPed)
    
    local dist = #(targetCoords - srcCoords)

    if dist > 3.0 then
        debugPrint("cylex_animmenuv2:server:sendAnimationInvite: Triggered with out of distance!", dist)
        return
    end

    debugPrint(json.encode(data))

    if not animationInvites[src] then
        animationInvites[src] = {
            anim = data.animation,
            source = src,
            target = target
        }
        
        SetTimeout(5000, function()
            if animationInvites[src] then
                animationInvites[src] = nil
            end
        end)

        TriggerClientEvent("cylex_animmenuv2:client:getAnimationInvite", targetId, data.animation, src)
    else
        debugPrint("cylex_animmenuv2:server:sendAnimationInvite: Target already has an invite!", target)
    end
end)

RegisterNetEvent("cylex_animmenuv2:server:acceptAnimationInvite", function(targetSrc, animationData)
    local src = source
    local target = tonumber(targetSrc)

    if not target then return end
    if not GetPlayerName(target) then return end

    local ped = GetPlayerPed(target)
    local srcPed = GetPlayerPed(src)
    local targetCoords = GetEntityCoords(ped)
    local srcCoords = GetEntityCoords(srcPed)

    local dist = #(srcCoords - targetCoords)
    if dist > 3.0 then
        debugPrint("cylex_animmenuv2:server:acceptAnimationInvite: Triggered with out of distance!", dist)
        return
    end

    if animationInvites[targetSrc] then
        TriggerClientEvent("cylex_animmenuv2:client:playSyncedAnim", src, animationData, targetSrc)
        TriggerClientEvent("cylex_animmenuv2:client:playSyncedAnimSource", targetSrc, animationInvites[targetSrc].anim, src)
        
        if animationInvites[targetSrc] then
            animationInvites[targetSrc] = nil
        end
        if animationInvites[target] then
            animationInvites[target] = nil
        end
    end
end)

AddEventHandler("playerDropped", function(reason)
    local src = source
    animationInvites[src] = nil
end)
