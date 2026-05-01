-- Variables
local QBCore = exports['qb-core']:GetCoreObject()
local PlayerData = QBCore.Functions.GetPlayerData()
local CurrentWeaponData, CanShoot, MultiplierAmount, currentWeapon = {}, true, 0, nil

-- Handlers

AddEventHandler('QBCore:Client:OnPlayerLoaded', function()
    PlayerData = QBCore.Functions.GetPlayerData()

    Wait(1000)

    local ped = PlayerPedId()
    RemoveAllPedWeapons(ped, true)
    currentWeapon = nil

    QBCore.Functions.TriggerCallback('qb-weapons:server:GetConfig', function(RepairPoints)
        for k, data in pairs(RepairPoints) do
            Config.WeaponRepairPoints[k].IsRepairing = data.IsRepairing
            Config.WeaponRepairPoints[k].RepairingData = data.RepairingData
        end
    end)
end)

RegisterNetEvent('QBCore:Client:OnPlayerUnload', function()
    for k in pairs(Config.WeaponRepairPoints) do
        Config.WeaponRepairPoints[k].IsRepairing = false
        Config.WeaponRepairPoints[k].RepairingData = {}
    end
end)

-- Functions

local function DrawText3Ds(x, y, z, text)
    SetTextScale(0.35, 0.35)
    SetTextFont(4)
    SetTextProportional(1)
    SetTextColour(255, 255, 255, 215)
    BeginTextCommandDisplayText('STRING')
    SetTextCentre(true)
    AddTextComponentSubstringPlayerName(text)
    SetDrawOrigin(x, y, z, 0)
    EndTextCommandDisplayText(0.0, 0.0)
    local factor = (string.len(text)) / 370
    DrawRect(0.0, 0.0 + 0.0125, 0.017 + factor, 0.03, 0, 0, 0, 75)
    ClearDrawOrigin()
end

-- Events

RegisterNetEvent('qb-weapons:client:SyncRepairShops', function(NewData, key)
    Config.WeaponRepairPoints[key].IsRepairing = NewData.IsRepairing
    Config.WeaponRepairPoints[key].RepairingData = NewData.RepairingData
end)

RegisterNetEvent('qb-weapons:client:EquipTint', function(weapon, tint)
    local player = PlayerPedId()
    SetPedWeaponTintIndex(player, weapon, tint)
end)

RegisterNetEvent('qb-weapons:client:SetCurrentWeapon', function(data, bool)
    if data ~= false then
        CurrentWeaponData = data
    else
        CurrentWeaponData = {}
    end
    CanShoot = bool
end)

RegisterNetEvent('qb-weapons:client:SetWeaponQuality', function(amount)
    if CurrentWeaponData and next(CurrentWeaponData) then
        TriggerServerEvent('qb-weapons:server:SetWeaponQuality', CurrentWeaponData, amount)
    end
end)

RegisterNetEvent('qb-weapons:client:AddAmmo', function(ammoType, amount, itemData)
    local ped = PlayerPedId()
    local weapon = GetSelectedPedWeapon(ped)

    if not CurrentWeaponData then
        QBCore.Functions.Notify(Lang:t('error.no_weapon'), 'error')
        return
    end

    if QBCore.Shared.Weapons[weapon]['name'] == 'weapon_unarmed' then
        QBCore.Functions.Notify(Lang:t('error.no_weapon_in_hand'), 'error')
        return
    end

    if QBCore.Shared.Weapons[weapon]['ammotype'] ~= ammoType:upper() then
        QBCore.Functions.Notify(Lang:t('error.wrong_ammo'), 'error')
        return
    end

    local total = GetAmmoInPedWeapon(ped, weapon)
    local _, maxAmmo = GetMaxAmmo(ped, weapon)

    if total >= maxAmmo then
        QBCore.Functions.Notify(Lang:t('error.max_ammo'), 'error')
        return
    end

    QBCore.Functions.Progressbar('taking_bullets', Lang:t('info.loading_bullets'), Config.ReloadTime, false, true, {
        disableMovement = false,
        disableCarMovement = false,
        disableMouse = false,
        disableCombat = true,
    }, {}, {}, {}, function()
        weapon = GetSelectedPedWeapon(ped)

        if QBCore.Shared.Weapons[weapon]?.ammotype ~= ammoType then
            return QBCore.Functions.Notify(Lang:t('error.wrong_ammo'), 'error')
        end

        AddAmmoToPed(ped, weapon, amount)
        TaskReloadWeapon(ped, false)

        TriggerServerEvent('qb-weapons:server:UpdateWeaponAmmo', CurrentWeaponData, total + amount)
        TriggerServerEvent('qb-weapons:server:removeWeaponAmmoItem', itemData)

        TriggerEvent('qb-inventory:client:ItemBox', QBCore.Shared.Items[itemData.name], 'remove')
        TriggerEvent('QBCore:Notify', Lang:t('success.reloaded'), 'success')
    end, function()
        QBCore.Functions.Notify(Lang:t('error.canceled'), 'error')
    end)
end)

-- 🔥 CLEAN USE WEAPON (FIXED)

RegisterNetEvent('qb-weapons:client:UseWeapon', function(weaponData, shootbool)
    local ped = PlayerPedId()
    local weaponName = tostring(weaponData.name)

    if currentWeapon == weaponName then
        SetCurrentPedWeapon(ped, `WEAPON_UNARMED`, true)
        currentWeapon = nil
        return
    end

    TriggerEvent('qb-weapons:client:SetCurrentWeapon', weaponData, shootbool)

    local weaponHash = joaat(weaponName)
    local ammo = tonumber(weaponData.info.ammo) or 0

    if weaponName == 'weapon_petrolcan' or weaponName == 'weapon_fireextinguisher' then
        ammo = 4000
    end

    -- 🔥 ONLY THIS
    GiveWeaponToPed(ped, weaponHash, ammo, false, false)
    SetCurrentPedWeapon(ped, weaponHash, true)

    currentWeapon = weaponName
end)


RegisterNetEvent('qb-weapons:client:CheckWeapon', function(weaponName)
    if currentWeapon ~= weaponName:lower() then return end
    local ped = PlayerPedId()
    TriggerEvent('qb-weapons:ResetHolster')
    SetCurrentPedWeapon(ped, `WEAPON_UNARMED`, true)
    RemoveAllPedWeapons(ped, true)
    currentWeapon = nil
end)

-- Threads

CreateThread(function()
    SetWeaponsNoAutoswap(true)
end)


local lastAmmo = -1
local lastWeapon = nil

CreateThread(function()
    while true do
        local ped = PlayerPedId()

        if IsPedArmed(ped, 7) == 1 then
            local weaponHash = GetSelectedPedWeapon(ped)

            if weaponHash and weaponHash ~= `WEAPON_UNARMED` then
                local ammo = GetAmmoInPedWeapon(ped, weaponHash)
                local weaponData = QBCore.Shared.Weapons[weaponHash]

                if weaponData then
                    local weaponName = weaponData.name

                    -- ✅ ONLY SAVE IF SOMETHING CHANGED
                    if ammo ~= lastAmmo or weaponName ~= lastWeapon then
                        lastAmmo = ammo
                        lastWeapon = weaponName

                        TriggerServerEvent('qb-weapons:server:UpdateWeaponAmmo', weaponName, ammo)
                    end
                end
            end
        else
            lastAmmo = -1
            lastWeapon = nil
        end

        Wait(200)
    end
end)