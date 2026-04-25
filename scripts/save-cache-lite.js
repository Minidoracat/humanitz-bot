#!/usr/bin/env node
/**
 * HumanitZ lightweight save cache exporter.
 *
 * This is a trimmed, bot-focused variant of the full humanitz-agent.js parser.
 * It reads the GVAS save directly and writes only the fields needed by the
 * Discord bot's save-backed commands.
 *
 * Usage:
 *   node scripts/save-cache-lite.js
 *   node scripts/save-cache-lite.js --save /path/Save_DedicatedSaveMP.sav
 *   node scripts/save-cache-lite.js --output tmp/save-cache-lite.json
 *
 * Notes:
 * - This still reads the whole .sav into memory. Run it low-frequency, under
 *   flock/systemd limits, and never as a tight watch loop.
 * - It intentionally skips world structures, vehicles, containers, AI, and
 *   inventory detail to avoid the heavy 4GB+ JSON path.
 */
'use strict';

const fs = require('fs');
const path = require('path');

const DEFAULT_SAVE =
  '/home/hzserver/serverfiles/HumanitZServer/Saved/SaveGames/SaveList/Default/Save_DedicatedSaveMP.sav';
const DEFAULT_OUTPUT = path.resolve(process.cwd(), 'tmp/save-cache-lite.json');

const CAPTURE_ARRAYS = new Set(['DropInSaves', 'Statistics', 'ExtendedStats']);
const CAPTURE_MAPS = new Set(['GameStats', 'FloatData']);

const PERK_MAP = {
  'Enum_Professions::NewEnumerator0': 'Unemployed',
  'Enum_Professions::NewEnumerator1': 'Amateur Boxer',
  'Enum_Professions::NewEnumerator2': 'Farmer',
  'Enum_Professions::NewEnumerator3': 'Mechanic',
  'Enum_Professions::NewEnumerator9': 'Car Salesman',
  'Enum_Professions::NewEnumerator10': 'Outdoorsman',
  'Enum_Professions::NewEnumerator12': 'Chemist',
  'Enum_Professions::NewEnumerator13': 'Emergency Medical Technician',
  'Enum_Professions::NewEnumerator14': 'Military Veteran',
  'Enum_Professions::NewEnumerator15': 'Thief',
  'Enum_Professions::NewEnumerator16': 'Fire Fighter',
  'Enum_Professions::NewEnumerator17': 'Electrical Engineer',
};

const STAT_TAG_MAP = {
  'statistics.stat.game.kills.total': 'zombiesKilled',
  'statistics.stat.game.kills.headshot': 'headshots',
  'statistics.stat.game.kills.type.melee': 'meleeKills',
  'statistics.stat.game.kills.type.ranged': 'gunKills',
  'statistics.stat.game.kills.type.blast': 'blastKills',
  'statistics.stat.game.kills.type.unarmed': 'fistKills',
  'statistics.stat.game.kills.type.takedown': 'takedownKills',
  'statistics.stat.game.kills.type.vehicle': 'vehicleKills',
  'statistics.stat.progress.survivefor3days': 'survivalDays',
  'statistics.stat.game.bitten': 'timesBitten',
  'statistics.stat.game.activity.FishCaught': 'fishCaught',
};

function createReader(buf) {
  let offset = 0;
  return {
    buf,
    get length() {
      return buf.length;
    },
    getOffset: () => offset,
    setOffset: (value) => {
      offset = value;
    },
    remaining: () => buf.length - offset,
    skip: (bytes) => {
      offset += bytes;
    },
    readU8: () => buf[offset++],
    readU16: () => {
      const value = buf.readUInt16LE(offset);
      offset += 2;
      return value;
    },
    readU32: () => {
      const value = buf.readUInt32LE(offset);
      offset += 4;
      return value;
    },
    readI32: () => {
      const value = buf.readInt32LE(offset);
      offset += 4;
      return value;
    },
    readI64: () => {
      const lo = buf.readUInt32LE(offset);
      const hi = buf.readInt32LE(offset + 4);
      offset += 8;
      return Number(BigInt(hi) * 0x100000000n + BigInt(lo >>> 0));
    },
    readF32: () => {
      const value = buf.readFloatLE(offset);
      offset += 4;
      return value;
    },
    readF64: () => {
      const value = buf.readDoubleLE(offset);
      offset += 8;
      return value;
    },
    readGuid: () => {
      const value = buf.subarray(offset, offset + 16).toString('hex');
      offset += 16;
      return value;
    },
    readBool: () => buf[offset++] !== 0,
    readFString: () => {
      const len = buf.readInt32LE(offset);
      offset += 4;
      if (len === 0) return '';
      if (len > 0 && len < 65536) {
        const value = buf.toString('utf8', offset, offset + len - 1);
        offset += len;
        return value;
      }
      if (len < 0 && len > -65536) {
        const chars = -len;
        const value = buf.toString('utf16le', offset, offset + (chars - 1) * 2);
        offset += chars * 2;
        return value;
      }
      throw new Error(`Bad FString length ${len} at ${offset - 4}`);
    },
  };
}

function cleanName(name) {
  return name.replace(/_\d+_[A-F0-9]{32}$/i, '');
}

function parseHeader(r) {
  const magic = Buffer.from([r.readU8(), r.readU8(), r.readU8(), r.readU8()]).toString('ascii');
  if (magic !== 'GVAS') throw new Error('Not a GVAS save file');

  const header = {
    magic,
    saveVersion: r.readU32(),
    packageVersion: r.readU32(),
    engineVersion: {
      major: r.readU16(),
      minor: r.readU16(),
      patch: r.readU16(),
    },
    build: r.readU32(),
    branch: r.readFString(),
    customVersions: [],
  };

  r.readU32();
  const customVersionCount = r.readU32();
  for (let i = 0; i < customVersionCount; i += 1) {
    header.customVersions.push({ guid: r.readGuid(), version: r.readI32() });
  }
  header.saveClass = r.readFString();
  return header;
}

function readProperty(r) {
  if (r.remaining() < 4) return null;
  const start = r.getOffset();

  let rawName;
  let typeName;
  try {
    rawName = r.readFString();
    if (!rawName || rawName === 'None') return null;
    typeName = r.readFString();
  } catch {
    r.setOffset(start);
    return null;
  }

  const dataSize = r.readI64();
  if (dataSize < 0 || dataSize > r.length) {
    r.setOffset(start);
    return null;
  }

  const name = cleanName(rawName);
  const prop = { name, raw: rawName, type: typeName };

  try {
    switch (typeName) {
      case 'BoolProperty':
        prop.value = r.readBool();
        r.readU8();
        break;
      case 'IntProperty':
        r.readU8();
        prop.value = r.readI32();
        break;
      case 'UInt32Property':
        r.readU8();
        prop.value = r.readU32();
        break;
      case 'Int64Property':
        r.readU8();
        prop.value = r.readI64();
        break;
      case 'FloatProperty':
        r.readU8();
        prop.value = r.readF32();
        break;
      case 'DoubleProperty':
        r.readU8();
        prop.value = r.readF64();
        break;
      case 'StrProperty':
      case 'NameProperty':
      case 'SoftObjectProperty':
      case 'ObjectProperty':
        r.readU8();
        prop.value = r.readFString();
        break;
      case 'EnumProperty':
        prop.enumType = r.readFString();
        r.readU8();
        prop.value = r.readFString();
        break;
      case 'ByteProperty': {
        const enumName = r.readFString();
        r.readU8();
        if (enumName === 'None') prop.value = r.readU8();
        else {
          prop.enumType = enumName;
          prop.value = r.readFString();
        }
        break;
      }
      case 'StructProperty':
        readStructProperty(r, prop);
        break;
      case 'ArrayProperty':
        readArrayProperty(r, prop, dataSize);
        break;
      case 'MapProperty':
        readMapProperty(r, prop, dataSize);
        break;
      case 'TextProperty':
      case 'SetProperty':
      default:
        r.readU8();
        r.setOffset(r.getOffset() + dataSize);
        prop.value = null;
        break;
    }
  } catch {
    r.setOffset(start);
    return null;
  }

  return prop;
}

function readStructProperty(r, prop) {
  const structType = r.readFString();
  r.readGuid();
  r.readU8();
  prop.structType = structType;

  if (structType === 'Vector' || structType === 'Rotator') {
    prop.value = { x: r.readF32(), y: r.readF32(), z: r.readF32() };
    return;
  }
  if (structType === 'Quat') {
    prop.value = { x: r.readF32(), y: r.readF32(), z: r.readF32(), w: r.readF32() };
    return;
  }
  if (structType === 'Guid') {
    prop.value = r.readGuid();
    return;
  }
  if (structType === 'LinearColor') {
    prop.value = { r: r.readF32(), g: r.readF32(), b: r.readF32(), a: r.readF32() };
    return;
  }
  if (structType === 'DateTime' || structType === 'Timespan') {
    prop.value = r.readI64();
    return;
  }
  if (structType === 'Vector2D') {
    prop.value = { x: r.readF32(), y: r.readF32() };
    return;
  }
  if (structType === 'GameplayTagContainer') {
    const count = r.readU32();
    prop.value = [];
    for (let i = 0; i < count; i += 1) prop.value.push(r.readFString());
    return;
  }
  if (structType === 'Transform') {
    const children = [];
    let child;
    while ((child = readProperty(r)) !== null) children.push(child);
    const translation = children.find((item) => item.name === 'Translation');
    const rotation = children.find((item) => item.name === 'Rotation');
    prop.children = children;
    prop.value = {
      translation: translation?.value || null,
      rotation: rotation?.value || null,
    };
    return;
  }

  const children = [];
  let child;
  while ((child = readProperty(r)) !== null) children.push(child);
  prop.children = children;
  prop.value = 'struct';
}

function readArrayProperty(r, prop, dataSize) {
  const innerType = r.readFString();
  r.readU8();
  const afterSeparator = r.getOffset();
  const count = r.readI32();
  prop.innerType = innerType;
  prop.count = count;

  if (!CAPTURE_ARRAYS.has(prop.name)) {
    r.setOffset(afterSeparator + dataSize);
    prop.value = null;
    return;
  }

  if (innerType === 'StructProperty') {
    r.readFString();
    r.readFString();
    r.readI64();
    const structType = r.readFString();
    r.readGuid();
    r.readU8();
    prop.arrayStructType = structType;

    if (structType === 'Guid') {
      prop.value = [];
      for (let i = 0; i < count; i += 1) prop.value.push(r.readGuid());
    } else if (structType === 'Vector' || structType === 'Rotator') {
      prop.value = [];
      for (let i = 0; i < count; i += 1) {
        prop.value.push({ x: r.readF32(), y: r.readF32(), z: r.readF32() });
      }
    } else if (structType === 'Quat') {
      prop.value = [];
      for (let i = 0; i < count; i += 1) {
        prop.value.push({ x: r.readF32(), y: r.readF32(), z: r.readF32(), w: r.readF32() });
      }
    } else if (structType === 'LinearColor') {
      prop.value = [];
      for (let i = 0; i < count; i += 1) {
        prop.value.push({ r: r.readF32(), g: r.readF32(), b: r.readF32(), a: r.readF32() });
      }
    } else if (structType === 'DateTime' || structType === 'Timespan') {
      prop.value = [];
      for (let i = 0; i < count; i += 1) prop.value.push(r.readI64());
    } else if (structType === 'Vector2D') {
      prop.value = [];
      for (let i = 0; i < count; i += 1) prop.value.push({ x: r.readF32(), y: r.readF32() });
    } else {
      const values = [];
      for (let i = 0; i < count; i += 1) {
        const children = [];
        let child;
        while ((child = readProperty(r)) !== null) children.push(child);
        values.push(children);
      }
      prop.value = values;
    }
    return;
  }

  if (innerType === 'NameProperty' || innerType === 'StrProperty' || innerType === 'ObjectProperty') {
    prop.value = [];
    for (let i = 0; i < count; i += 1) prop.value.push(r.readFString());
    return;
  }
  if (innerType === 'IntProperty') {
    prop.value = [];
    for (let i = 0; i < count; i += 1) prop.value.push(r.readI32());
    return;
  }
  if (innerType === 'FloatProperty') {
    prop.value = [];
    for (let i = 0; i < count; i += 1) prop.value.push(r.readF32());
    return;
  }
  if (innerType === 'BoolProperty') {
    prop.value = [];
    for (let i = 0; i < count; i += 1) prop.value.push(r.readBool());
    return;
  }
  if (innerType === 'ByteProperty') {
    prop.value = [];
    for (let i = 0; i < count; i += 1) prop.value.push(r.readU8());
    return;
  }
  if (innerType === 'EnumProperty') {
    prop.value = [];
    for (let i = 0; i < count; i += 1) prop.value.push(r.readFString());
    return;
  }
  if (innerType === 'UInt32Property') {
    prop.value = [];
    for (let i = 0; i < count; i += 1) prop.value.push(r.readU32());
    return;
  }

  r.setOffset(afterSeparator + dataSize);
  prop.value = null;
}

function readMapProperty(r, prop, dataSize) {
  const keyType = r.readFString();
  const valueType = r.readFString();
  r.readU8();
  const afterSeparator = r.getOffset();
  prop.keyType = keyType;
  prop.valueType = valueType;

  if (!CAPTURE_MAPS.has(prop.name)) {
    r.setOffset(afterSeparator + dataSize);
    prop.value = null;
    return;
  }

  r.readI32();
  const count = r.readI32();
  const values = {};
  for (let i = 0; i < count; i += 1) {
    let key;
    if (keyType === 'StrProperty' || keyType === 'NameProperty') key = r.readFString();
    else if (keyType === 'IntProperty') key = String(r.readI32());
    else if (keyType === 'EnumProperty') key = r.readFString();
    else {
      r.setOffset(afterSeparator + dataSize);
      prop.value = null;
      return;
    }

    let value;
    if (valueType === 'FloatProperty') value = r.readF32();
    else if (valueType === 'IntProperty') value = r.readI32();
    else if (valueType === 'StrProperty' || valueType === 'NameProperty') value = r.readFString();
    else if (valueType === 'BoolProperty') value = r.readBool();
    else {
      r.setOffset(afterSeparator + dataSize);
      prop.value = null;
      return;
    }
    values[key] = value;
  }
  prop.value = values;
}

function recoverForward(r, startPos, maxScan = 500000) {
  const buf = r.buf;
  const limit = Math.min(startPos + maxScan, buf.length - 10);
  for (let scan = startPos + 1; scan < limit; scan += 1) {
    const len = buf.readInt32LE(scan);
    if (len > 3 && len < 80) {
      const peek = buf.toString('utf8', scan + 4, scan + 4 + len - 1);
      if (/^[A-Z][a-zA-Z0-9_]{2,60}$/.test(peek)) {
        r.setOffset(scan);
        return true;
      }
    }
  }
  return false;
}

function createPlayer() {
  return {
    steamId: '',
    x: null,
    y: null,
    z: null,
    health: 0,
    hunger: 0,
    thirst: 0,
    stamina: 0,
    infection: 0,
    bites: 0,
    survivalDays: 0,
    profession: '',
    isMale: true,
    zombiesKilled: 0,
    headshots: 0,
    meleeKills: 0,
    gunKills: 0,
    blastKills: 0,
    fistKills: 0,
    vehicleKills: 0,
    takedownKills: 0,
    fishCaught: 0,
    timesBitten: 0,
  };
}

function parseSave(buf) {
  const r = createReader(buf);
  const header = parseHeader(r);
  const players = new Map();
  const worldState = {};
  let currentSteamId = null;

  function ensurePlayer(steamId) {
    if (!players.has(steamId)) {
      const player = createPlayer();
      player.steamId = steamId;
      players.set(steamId, player);
    }
    return players.get(steamId);
  }

  function prescanSteamId(children) {
    for (const child of children) {
      if (child?.name === 'SteamID' && typeof child.value === 'string') {
        const match = child.value.match(/(7656\d+)/);
        if (match) {
          currentSteamId = match[1];
          return;
        }
      }
    }
  }

  function handleProperty(prop) {
    if (!prop) return;
    const name = prop.name;

    if (name === 'SteamID' && typeof prop.value === 'string') {
      const match = prop.value.match(/(7656\d+)/);
      if (match) currentSteamId = match[1];
    }

    if (prop.children) {
      prescanSteamId(prop.children);
      for (const child of prop.children) handleProperty(child);
    }

    if (Array.isArray(prop.value) && prop.value.length > 0 && Array.isArray(prop.value[0])) {
      if (name === 'Statistics' && currentSteamId) {
        extractStatistics(prop.value, ensurePlayer(currentSteamId));
      }
      for (const element of prop.value) {
        prescanSteamId(element);
        for (const child of element) handleProperty(child);
      }
      if (name === 'DropInSaves') currentSteamId = null;
    }

    if (name === 'Dedi_DaysPassed' && typeof prop.value === 'number') {
      worldState.daysPassed = prop.value;
    }
    if ((name === 'SeasonDay' || name === 'CurrentSeasonDay') && typeof prop.value === 'number') {
      worldState.seasonDay = prop.value;
    }
    if (name === 'TotalDaysElapsed' && typeof prop.value === 'number') {
      worldState.totalDaysElapsed = prop.value;
    }

    if (!currentSteamId) return;
    const player = ensurePlayer(currentSteamId);

    if (name === 'DayzSurvived' && typeof prop.value === 'number') player.survivalDays = prop.value;
    if (name === 'Male') player.isMale = !!prop.value;
    if (name === 'Bites' && typeof prop.value === 'number') player.bites = prop.value;

    if (name === 'CurrentHealth' && typeof prop.value === 'number') player.health = round(prop.value);
    if (name === 'CurrentHunger' && typeof prop.value === 'number') player.hunger = round(prop.value);
    if (name === 'CurrentThirst' && typeof prop.value === 'number') player.thirst = round(prop.value);
    if (name === 'CurrentStamina' && typeof prop.value === 'number') player.stamina = round(prop.value);
    if (name === 'CurrentInfection' && typeof prop.value === 'number') player.infection = round(prop.value);

    if (name === 'StartingPerk' && typeof prop.value === 'string') {
      player.profession = PERK_MAP[prop.value] || prop.value;
    }

    if (name === 'GameStats' && prop.value && typeof prop.value === 'object') {
      const stats = prop.value;
      if (stats.ZeeksKilled !== undefined) player.zombiesKilled = stats.ZeeksKilled;
      if (stats.HeadShot !== undefined) player.headshots = stats.HeadShot;
      if (stats.MeleeKills !== undefined) player.meleeKills = stats.MeleeKills;
      if (stats.GunKills !== undefined) player.gunKills = stats.GunKills;
      if (stats.BlastKills !== undefined) player.blastKills = stats.BlastKills;
      if (stats.FistKills !== undefined) player.fistKills = stats.FistKills;
      if (stats.VehicleKills !== undefined) player.vehicleKills = stats.VehicleKills;
      if (stats.TakedownKills !== undefined) player.takedownKills = stats.TakedownKills;
      if (stats.DaysSurvived !== undefined && stats.DaysSurvived > 0) {
        player.survivalDays = stats.DaysSurvived;
      }
    }

    if (name === 'FloatData' && prop.value && typeof prop.value === 'object') {
      const floats = prop.value;
      if (floats.InfectionBuildup !== undefined) player.infection = round(floats.InfectionBuildup);
    }

    if (name === 'PlayerTransform' && prop.structType === 'Transform') {
      extractTransform(prop, player);
    } else if (
      player.x === null &&
      prop.structType === 'Transform' &&
      prop.value?.translation &&
      !['PlayerRespawnPoint', 'BackpackTransform', 'CompanionTransform'].includes(name)
    ) {
      extractTransform(prop, player);
    }
  }

  while (r.remaining() > 4) {
    try {
      const saved = r.getOffset();
      const prop = readProperty(r);
      if (prop === null) {
        if (r.getOffset() === saved && !recoverForward(r, saved)) break;
        continue;
      }
      handleProperty(prop);
    } catch {
      const pos = r.getOffset();
      if (!recoverForward(r, pos)) break;
    }
  }

  return { header, players: Object.fromEntries(players), worldState };
}

function extractStatistics(statArray, player) {
  for (const element of statArray) {
    let tagName = null;
    let currentValue = null;

    for (const prop of element) {
      if (prop.name === 'StatisticId') {
        if (typeof prop.value === 'string' && prop.value.startsWith('statistics.')) tagName = prop.value;
        if (prop.children) {
          const tag = prop.children.find((child) => child.name === 'TagName');
          if (typeof tag?.value === 'string') tagName = tag.value;
        }
      }
      if (prop.name === 'CurrentValue' && typeof prop.value === 'number') {
        currentValue = prop.value;
      }
    }

    if (tagName && currentValue !== null && currentValue > 0) {
      const field = STAT_TAG_MAP[tagName];
      if (field) player[field] = Math.round(currentValue);
    }
  }
}

function extractTransform(prop, player) {
  const translation = prop.value?.translation;
  if (!translation || typeof translation.x !== 'number' || typeof translation.y !== 'number') return;
  player.x = round2(translation.x);
  player.y = round2(translation.y);
  player.z = round2(translation.z);
}

function buildOutput(savePath, result, startedAt, durationMs) {
  const players = result.players;
  const playerList = Object.values(players);
  const parsedAt = new Date().toISOString();
  const saveStat = fs.statSync(savePath);

  return {
    version: 1,
    source: 'save-cache-lite',
    parsedAt,
    parseDurationMs: durationMs,
    saveFile: savePath,
    saveFileMtime: saveStat.mtime.toISOString(),
    saveFileSize: saveStat.size,
    startedAt,
    playerCount: playerList.length,
    worldState: result.worldState,
    players,
    leaderboards: {
      survivalDays: playerList
        .slice()
        .sort((a, b) => (b.survivalDays || 0) - (a.survivalDays || 0))
        .slice(0, 10)
        .map(pickLeaderboardPlayer),
      kills: playerList
        .slice()
        .sort((a, b) => (b.zombiesKilled || 0) - (a.zombiesKilled || 0))
        .slice(0, 10)
        .map(pickLeaderboardPlayer),
    },
  };
}

function pickLeaderboardPlayer(player) {
  return {
    steamId: player.steamId,
    survivalDays: player.survivalDays,
    zombiesKilled: player.zombiesKilled,
    headshots: player.headshots,
  };
}

function parseArgs(argv) {
  const opts = { save: DEFAULT_SAVE, output: DEFAULT_OUTPUT, pretty: false, help: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--save' || arg === '-s') opts.save = argv[++i] || '';
    else if (arg === '--output' || arg === '-o') opts.output = argv[++i] || '';
    else if (arg === '--pretty') opts.pretty = true;
    else if (arg === '--help' || arg === '-h') opts.help = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  return opts;
}

function printHelp() {
  console.log(`Usage: node scripts/save-cache-lite.js [options]

Options:
  --save, -s <path>      Save_DedicatedSaveMP.sav path
  --output, -o <path>    Output JSON path (default: tmp/save-cache-lite.json)
  --pretty              Pretty-print JSON
  --help, -h            Show this help
`);
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help) {
    printHelp();
    return;
  }
  if (!opts.save || !fs.existsSync(opts.save)) throw new Error(`Save file not found: ${opts.save}`);

  const startedAt = new Date().toISOString();
  const start = Date.now();
  const buf = fs.readFileSync(opts.save);
  const parsed = parseSave(buf);
  const output = buildOutput(opts.save, parsed, startedAt, Date.now() - start);

  fs.mkdirSync(path.dirname(opts.output), { recursive: true });
  const tmpPath = `${opts.output}.tmp`;
  fs.writeFileSync(tmpPath, JSON.stringify(output, null, opts.pretty ? 2 : 0));
  fs.renameSync(tmpPath, opts.output);

  console.log(
    `[save-cache-lite] players=${output.playerCount} output=${opts.output} duration=${output.parseDurationMs}ms`,
  );
}

function round(value) {
  return Math.round(value);
}

function round2(value) {
  return Math.round(value * 100) / 100;
}

if (require.main === module) {
  try {
    main();
  } catch (error) {
    console.error(`[save-cache-lite] ${error.message}`);
    process.exitCode = 1;
  }
}
