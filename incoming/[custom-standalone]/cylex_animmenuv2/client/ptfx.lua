local function PtfxThis(asset)
    while not HasNamedPtfxAssetLoaded(asset) do
        RequestNamedPtfxAsset(asset)
        Wait(10)
    end
    UseParticleFxAsset(asset)
end

function PtfxStart()
    LocalPlayer.state:set("ptfx", true, true)
end

function PtfxStop()
    LocalPlayer.state:set("ptfx", false, true)
end

AddStateBagChangeHandler("ptfx", nil, function(bagName, key, value, _unused, replicated)
    local serverId = tonumber(bagName:gsub("player:", ""), 10)
    if not PlayerParticles[serverId] and not value then return end -- If no particle and stopping, ignore
    
    local playerId = GetPlayerFromServerId(serverId)
    if playerId == 0 then return end
    
    local ped = GetPlayerPed(playerId)
    if not DoesEntityExist(ped) then return end
    
    local state = Player(serverId).state
    if value then
        local asset = state.ptfxAsset
        local name = state.ptfxName
        local offset = state.ptfxOffset
        local rot = state.ptfxRot
        local boneIndex = GetEntityBoneIndexByName(name, "VFX")
        
        if state.ptfxBone then
            local bIndex = GetPedBoneIndex(ped, state.ptfxBone)
            if bIndex then
                boneIndex = bIndex
            end
        end
        
        local scale = state.ptfxScale or 1
        local color = state.ptfxColor
        local propNet = state.ptfxPropNet
        local entityTarget = ped
        
        if propNet then
            local propObj = NetToObj(propNet)
            if DoesEntityExist(propObj) then
                entityTarget = propObj
            end
        end
        
        PtfxThis(asset)
        local particle = StartNetworkedParticleFxLoopedOnEntityBone(name, entityTarget, offset.x, offset.y, offset.z, rot.x, rot.y, rot.z, boneIndex, scale + 0.0, 0, 0, 0, 1065353216, 1065353216, 1065353216, 0)
        PlayerParticles[serverId] = particle
        
        if color then
            if color[1] and type(color[1]) == "table" then
                local randomColor = color[math.random(1, #color)]
                color = randomColor
            end
            SetParticleFxLoopedAlpha(PlayerParticles[serverId], color.A)
            SetParticleFxLoopedColour(PlayerParticles[serverId], color.R / 255, color.G / 255, color.B / 255, false)
        end
        
        -- print("Started PTFX: " .. tostring(PlayerParticles[serverId]))
    else
        -- print("Stopped PTFX: " .. tostring(PlayerParticles[serverId]))
        StopParticleFxLooped(PlayerParticles[serverId], false)
        RemoveNamedPtfxAsset(state.ptfxAsset)
        PlayerParticles[serverId] = nil
    end
end)
