RegisterNetEvent("cylex_animmenuv2:server:syncAnimpos", function(pos, heading)
    TriggerClientEvent("cylex_animmenuv2:client:syncAnimpos", -1, source, pos, heading)
end)
