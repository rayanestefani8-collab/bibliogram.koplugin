--[[
BiblioGram — Plugin KOReader
Interface simples para controlar telegram-opds pelo Kobo.

Menu:
  - Configurar servidor (URL, pasta, sync, capas, títulos, credenciais)
  - Teste de conexão
  - Sobre

Config salva em ~/.config/koreader/bibliogram.json
O telegram-opds (Python) lê essa config.
]]

local DataStorage     = require("datastorage")
local InfoMessage     = require("ui/widget/infomessage")
local InputDialog     = require("ui/widget/inputdialog")
local UIManager       = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local util            = require("util")
local logger          = require("logger")
local json            = require("json") or require("cjson")
local _               = require("gettext")

local Device = require("device")
local Screen = Device.screen

local BiblioGram = WidgetContainer:extend{
    name        = "bibliogram",
    is_doc_only = false,
    config_file = DataStorage:getSettingsDir() .. "/bibliogram.json",
    config      = nil,
}

-- ── Configuração ──────────────────────────────────────────────────────────────
function BiblioGram:getConfigPath()
    return self.config_file
end

function BiblioGram:loadConfig()
    if self.config then return self.config end
    local path = self:getConfigPath()
    local f = io.open(path, "r")
    if f then
        local content = f:read("*a")
        f:close()
        local ok, cfg = pcall(json.decode, content)
        if ok and type(cfg) == "table" then
            self.config = cfg
            return cfg
        end
    end

    self.config = {
        server_url     = "http://192.168.1.100:8081",
        download_dir   = "/mnt/onboard/BiblioGram",
        sync_auto      = false,
        sync_frequency = 15,
        update_covers  = true,
        fix_titles     = true,
        api_id         = "",
        api_hash       = "",
        phone          = "",
    }
    self:saveConfig()
    return self.config
end

function BiblioGram:saveConfig()
    if not self.config then return end
    local path = self:getConfigPath()
    local dir = path:match("^(.*)/[^/]+$")
    if dir then util.makePath(dir) end

    local f = io.open(path, "w")
    if f then
        local ok, json_str = pcall(json.encode, self.config)
        if ok then f:write(json_str) end
        f:close()
    end
end

-- ── Injetar servidor nos plugins OPDS ─────────────────────────────────────────
function BiblioGram:injectServerToOPDS()
    local cfg = self:loadConfig()
    local base = cfg.server_url:gsub("/$", "")
    local url = base .. "/opds"
    
    -- Tenta injetar no OPDS nativo
    local LuaSettings = require("luasettings")
    local opds_path = DataStorage:getSettingsDir() .. "/opds.lua"
    local ok, opds_settings = pcall(LuaSettings.open, LuaSettings, opds_path)
    if ok then
        local servers = opds_settings:readSetting("servers") or {}
        local filtered = {}
        for _, s in ipairs(servers) do
            if not (s.url and s.url:find(base, 1, true)) then
                table.insert(filtered, s)
            end
        end
        table.insert(filtered, 1, { title = "BiblioGram 📚", url = url })
        opds_settings:saveSetting("servers", filtered)
        opds_settings:flush()
    end
    
    -- Tenta injetar no OPDSPlus
    local opds_plus_path = DataStorage:getSettingsDir() .. "/opds_plus.lua"
    ok, opds_settings = pcall(LuaSettings.open, LuaSettings, opds_plus_path)
    if ok then
        local servers = opds_settings:readSetting("servers") or {}
        local filtered = {}
        for _, s in ipairs(servers) do
            if not (s.url and s.url:find(base, 1, true)) then
                table.insert(filtered, s)
            end
        end
        table.insert(filtered, 1, { title = "BiblioGram 📚", url = url })
        opds_settings:saveSetting("servers", filtered)
        opds_settings:flush()
    end
end

function BiblioGram:openOPDS()
    self:injectServerToOPDS()
    UIManager:show(InfoMessage:new{
        text = _("Servidor BiblioGram adicionado!\n\n") ..
               _("Abra o menu do OPDS/OPDSPlus para ver o catálogo."),
        timeout = 3,
    })
end

-- ── Verificação de Servidor ───────────────────────────────────────────────────
function BiblioGram:checkServer(callback)
    local cfg = self:loadConfig()
    local url = cfg.server_url:gsub("/$", "") .. "/opds"
    local ok, result = pcall(function()
        local http = require("socket.http")
        local _, code = http.request(url)
        return code
    end)
    local success = ok and result and tonumber(result) and tonumber(result) < 400
    if not success then
        logger.warn("BiblioGram: erro HTTP", tostring(result), url)
    end
    if callback then callback(success) end
end

-- ── Menu de Configurações ─────────────────────────────────────────────────────
function BiblioGram:inputSetting(title, current_value, callback)
    local dialog
    dialog = InputDialog:new{
        title = title,
        input = tostring(current_value or ""),
        buttons = {{
            {
                text = _("Cancelar"),
                callback = function() UIManager:close(dialog) end,
            },
            {
                text = _("Salvar"),
                is_enter_default = true,
                callback = function()
                    local value = dialog:getInputText()
                    UIManager:close(dialog)
                    callback(value)
                end,
            },
        }},
    }
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

function BiblioGram:showSettingsMenu()
    local self_ref = self
    self:loadConfig()
    
    local cfg = self.config
    
    local menu_items = {
        {
            text = _("URL do servidor"),
            callback = function()
                self_ref:inputSetting(_("URL do servidor"), cfg.server_url, function(val)
                    cfg.server_url = val:gsub("/$", "")
                    self_ref:saveConfig()
                    UIManager:show(InfoMessage:new{ text = _("Salvo!"), timeout = 1 })
                end)
            end,
        },
        {
            text = _("Pasta de download"),
            callback = function()
                self_ref:inputSetting(_("Pasta de download"), cfg.download_dir, function(val)
                    cfg.download_dir = val
                    self_ref:saveConfig()
                    UIManager:show(InfoMessage:new{ text = _("Salvo!"), timeout = 1 })
                end)
            end,
        },
        {
            text = _("Sync automático: ") .. (cfg.sync_auto and "ON" or "OFF"),
            callback = function()
                cfg.sync_auto = not cfg.sync_auto
                self_ref:saveConfig()
                UIManager:show(InfoMessage:new{ text = _("Alterado!"), timeout = 1 })
            end,
        },
        {
            text = _("Frequência sync (min)"),
            callback = function()
                self_ref:inputSetting(_("Frequência (minutos)"), cfg.sync_frequency, function(val)
                    cfg.sync_frequency = tonumber(val) or 15
                    self_ref:saveConfig()
                    UIManager:show(InfoMessage:new{ text = _("Salvo!"), timeout = 1 })
                end)
            end,
        },
        {
            text = _("Atualizar capas: ") .. (cfg.update_covers and "ON" or "OFF"),
            callback = function()
                cfg.update_covers = not cfg.update_covers
                self_ref:saveConfig()
                UIManager:show(InfoMessage:new{ text = _("Alterado!"), timeout = 1 })
            end,
        },
        {
            text = _("Limpar títulos: ") .. (cfg.fix_titles and "ON" or "OFF"),
            callback = function()
                cfg.fix_titles = not cfg.fix_titles
                self_ref:saveConfig()
                UIManager:show(InfoMessage:new{ text = _("Alterado!"), timeout = 1 })
            end,
        },
        {
            text = _("API_ID"),
            callback = function()
                self_ref:inputSetting(_("API_ID"), cfg.api_id, function(val)
                    cfg.api_id = val
                    self_ref:saveConfig()
                    UIManager:show(InfoMessage:new{ text = _("Salvo!"), timeout = 1 })
                end)
            end,
        },
        {
            text = _("API_HASH"),
            callback = function()
                self_ref:inputSetting(_("API_HASH"), cfg.api_hash, function(val)
                    cfg.api_hash = val
                    self_ref:saveConfig()
                    UIManager:show(InfoMessage:new{ text = _("Salvo!"), timeout = 1 })
                end)
            end,
        },
        {
            text = _("Telefone"),
            callback = function()
                self_ref:inputSetting(_("Telefone"), cfg.phone, function(val)
                    cfg.phone = val
                    self_ref:saveConfig()
                    UIManager:show(InfoMessage:new{ text = _("Salvo!"), timeout = 1 })
                end)
            end,
        },
        {
            text = _("Testar conexão"),
            callback = function()
                UIManager:show(InfoMessage:new{ text = _("Testando…"), timeout = 1 })
                self_ref:checkServer(function(ok)
                    UIManager:show(InfoMessage:new{
                        text = ok and _("Servidor OK ✓") or _("Servidor offline ✗"),
                        timeout = 2,
                    })
                end)
            end,
        },
    }

    local Menu = require("ui/widget/menu")
    local menu = Menu:new{
        title = _("Configurações"),
        item_table = menu_items,
        width = Screen:getWidth(),
        height = Screen:getHeight(),
    }
    UIManager:show(menu)
end

-- ── Registra o Plugin no Menu Principal ───────────────────────────────────────
function BiblioGram:init()
    self:loadConfig()
    if self.ui and self.ui.menu then
        self.ui.menu:registerToMainMenu(self)
    end
end

function BiblioGram:addToMainMenu(menu_items)
    local self_ref = self
    menu_items.bibliogram = {
        text = _("BiblioGram"),
        sub_item_table = {
            {
                text     = _("Adicionar ao OPDS"),
                callback = function() self_ref:openOPDS() end,
            },
            {
                text      = _("Configurações"),
                separator = true,
                callback  = function() self_ref:showSettingsMenu() end,
            },
            {
                text = _("Sobre"),
                callback = function()
                    local cfg = self_ref:loadConfig()
                    UIManager:show(InfoMessage:new{
                        text = "BiblioGram 📚\n\n"
                        .. _("Servidor: ") .. cfg.server_url .. "\n\n"
                        .. _("Config: ") .. self_ref:getConfigPath(),
                    })
                end,
            },
        },
    }
end

return BiblioGram
