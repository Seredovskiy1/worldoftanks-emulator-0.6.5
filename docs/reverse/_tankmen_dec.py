# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/common/items/tankmen.py
# Compiled at: 2011-05-26 15:49:22
import math, ResMgr, random, struct, nations, items
from items import _xml
from debug_utils import *
from constants import IS_CLIENT, ITEM_DEFS_PATH
from vehicles import VEHICLE_CLASS_TAGS
if IS_CLIENT:
    from helpers import i18n
SKILL_NAMES = (
 'reserved',
 'commander',
 'radioman',
 'driver',
 'gunner',
 'loader',
 'repair',
 'fireFighting',
 'camouflage',
 'reserved',
 'reserved',
 'reserved',
 'reserved',
 'reserved')
SKILL_INDICES = dict((x[1], x[0]) for x in enumerate(SKILL_NAMES) if not x[1].startswith('reserved'))
ROLES = frozenset(('commander', 'radioman', 'driver', 'gunner', 'loader'))
COMMON_SKILLS = frozenset(('repair', 'fireFighting', 'camouflage'))
PERKS = frozenset()
ROLES_AND_COMMON_SKILLS = ROLES | COMMON_SKILLS
COMMON_SKILLS_AND_PERKS = COMMON_SKILLS | PERKS
PERKS_BY_ROLES = {'commander': (frozenset()), 
   'radioman': (frozenset()), 
   'driver': (frozenset()), 
   'gunner': (frozenset()), 
   'loader': (frozenset())}
MAX_SKILL_LEVEL = 100
MIN_ROLE_LEVEL = 50
SKILL_LEVELS_PER_RANK = 50
COMMANDER_ADDITION_RATIO = 10
_MAX_FREE_XP = 2000000000
_LEVELUP_K1 = 50.0
_LEVELUP_K2 = 100.0

def init(preloadEverything):
    if preloadEverything:
        getSkillsConfig()
        for nationID in xrange(len(nations.NAMES)):
            getNationConfig(nationID)

    return


def getSkillsConfig():
    global _g_skillsConfig
    if _g_skillsConfig is None:
        _g_skillsConfig = _readSkillsConfig(ITEM_DEFS_PATH + 'tankmen/tankmen.xml')
    return _g_skillsConfig


def getNationConfig(nationID):
    global _g_nationsConfig
    if _g_nationsConfig[nationID] is None:
        _g_nationsConfig[nationID] = _readNationConfig(ITEM_DEFS_PATH + 'tankmen/' + nations.NAMES[nationID] + '.xml')
    return _g_nationsConfig[nationID]


def generatePassport(nationID, isPremium=False):
    isPremium = False
    groups = getNationConfig(nationID)['normalGroups' if not isPremium else 'premiumGroups']
    w = random.random()
    summWeight = 0.0
    for group in groups:
        weight = group['weight']
        if summWeight <= w < summWeight + weight:
            break
        summWeight += weight

    return (
     nationID,
     isPremium, group['isFemales'],
     random.choice(group['firstNamesList']),
     random.choice(group['lastNamesList']),
     random.choice(group['iconsList']))


def generateCompactDescr(passport, vehicleTypeID, role, roleLevel, skills=[], lastSkillLevel=MAX_SKILL_LEVEL, dossierCompactDescr=''):
    pack = struct.pack
    assert MIN_ROLE_LEVEL <= roleLevel <= MAX_SKILL_LEVEL
    (nationID, isPremium, isFemale, firstNameID, lastNameID, iconID) = passport
    header = items.ITEM_TYPE_INDICES['tankman'] + (nationID << 4)
    cd = pack('4B', header, vehicleTypeID, SKILL_INDICES[role], roleLevel)
    numSkills = len(skills)
    skills = [_[1] for s in skills]
    cd += pack((str(1 + numSkills) + 'B'), numSkills, *skills)
    cd += chr(lastSkillLevel if numSkills else 0)
    (rank, levelsToNextRank) = divmod(roleLevel - MIN_ROLE_LEVEL, SKILL_LEVELS_PER_RANK)
    levelsToNextRank = SKILL_LEVELS_PER_RANK - levelsToNextRank
    isFemale = 1 if isFemale else 0
    isPremium = 1 if isPremium else 0
    flags = isFemale | isPremium << 1
    cd += pack('<B3H2Bi', flags, firstNameID, lastNameID, iconID, rank, levelsToNextRank, 0)
    cd += dossierCompactDescr
    return cd


def stripNonBattle(compactDescr):
    return compactDescr[:6 + ord(compactDescr[4])]


def parseNationAndRole(compactDescr):
    return (
     ord(compactDescr[0]) >> 4 & 15,
     ord(compactDescr[2]))


def compareMastery(tankmanDescr1, tankmanDescr2):
    level1 = tankmanDescr1.roleLevel
    level2 = tankmanDescr2.roleLevel
    if level1 < level2:
        return -1
    if level1 > level2:
        return 1
    skills1 = len(tankmanDescr1.skills)
    skills2 = len(tankmanDescr2.skills)
    level1 = tankmanDescr1.lastSkillLevel if skills1 else MAX_SKILL_LEVEL
    level2 = tankmanDescr2.lastSkillLevel if skills2 else MAX_SKILL_LEVEL
    if skills1 < skills2 and level1 != MAX_SKILL_LEVEL:
        return -1
    if skills2 < skills1 and level2 != MAX_SKILL_LEVEL:
        return 1
    if level1 < level2:
        return -1
    if level1 > level2:
        return 1
    freeXP1 = tankmanDescr1.freeXP
    freeXP2 = tankmanDescr2.freeXP
    if freeXP1 < freeXP2:
        return -1
    if freeXP1 > freeXP2:
        return 1
    return 0


def fixObsoleteNames(compactDescr):
    cd = compactDescr
    header = ord(cd[0])
    assert header & 15 == items.ITEM_TYPE_INDICES['tankman']
    nationID = header >> 4 & 15
    conf = getNationConfig(nationID)
    namesOffset = ord(cd[4]) + 7
    (firstNameID, lastNameID) = struct.unpack('<2H', cd[namesOffset:namesOffset + 4])
    hasChanges = False
    if firstNameID not in conf['firstNames']:
        hasChanges = True
        firstNameID = generatePassport(nationID)[3]
    if lastNameID not in conf['lastNames']:
        hasChanges = True
        lastNameID = generatePassport(nationID)[4]
    if not hasChanges:
        return cd
    return cd[:namesOffset] + struct.pack('<2H', firstNameID, lastNameID) + cd[namesOffset + 4:]


def computeCrewSkillsEfficiency(vehicleDescr, tankmanDescrs, dynamicLevelIncrease=0):
    res = {}
    skillLevels = _computeSummSkillLevels(vehicleDescr, tankmanDescrs, dynamicLevelIncrease)
    commanderAddition = skillLevels['commander'] / COMMANDER_ADDITION_RATIO
    skillSlots = vehicleDescr.type.crewSkillSlots
    for (skillName, skillLevel) in skillLevels.iteritems():
        (slotsTotal, slotsWithoutCmd) = skillSlots[skillName]
        if slotsTotal != 0:
            skillLevel = (skillLevel + commanderAddition * slotsWithoutCmd) / slotsTotal
        else:
            assert skillLevel == 0
        res[skillName] = float(skillLevel) / MAX_SKILL_LEVEL

    return res


class TankmanDescr(object):

    def __init__(self, compactDescr, battleOnly=False):
        self.__initFromCompactDescr(compactDescr, battleOnly)
        return

    def efficiencyOnVehicle(self, vehicleDescr):
        (nationID, vehicleTypeID) = vehicleDescr.type.id
        assert nationID == self.nationID
        factor = 1.0
        if vehicleTypeID != self.vehicleTypeID:
            (isPremium, isSameClass) = self.__paramsOnVehicle(vehicleDescr.type)
            if isSameClass:
                factor = 1.0 if isPremium else 0.75
            else:
                factor = 0.75 if isPremium else 0.5
        addition = vehicleDescr.miscAttrs['crewLevelIncrease']
        return (
         factor, addition)

    def battleXpGain(self, xp, vehicleType, tankmanHasSurvived):
        (nationID, vehicleTypeID) = vehicleType.id
        assert nationID == self.nationID
        if vehicleTypeID != self.vehicleTypeID:
            (isPremium, isSameClass) = self.__paramsOnVehicle(vehicleType)
            if isPremium:
                xp *= 1.0 if isSameClass else 0.5
            else:
                xp *= 0.5 if isSameClass else 0.25
        if not tankmanHasSurvived:
            xp *= 0.9
        return int(xp)

    def levelUpXpCost(self, fromSkillLevel, skillSeqNum):
        baseCost = _LEVELUP_K1 * pow(_LEVELUP_K2, float(fromSkillLevel) / MAX_SKILL_LEVEL)
        return int(round(pow(2, skillSeqNum) * baseCost))

    def addXP(self, xp):
        self.freeXP = min(_MAX_FREE_XP, self.freeXP + xp)
        while self.roleLevel < MAX_SKILL_LEVEL:
            xpCost = self.levelUpXpCost(self.roleLevel, 0)
            if xpCost > self.freeXP:
                break
            self.freeXP -= xpCost
            self.roleLevel += 1
            self.__updateRankAtSkillLevelUp()

        if self.roleLevel == MAX_SKILL_LEVEL and self.skills:
            self.__levelUpLastSkill()
        return

    def addSkill(self, skillName):
        if skillName in self.skills:
            raise ValueError
        if skillName not in COMMON_SKILLS and skillName not in PERKS_BY_ROLES[self.role]:
            raise ValueError, skillName
        if self.roleLevel != MAX_SKILL_LEVEL:
            raise ValueError, self.roleLevel
        if self.skills and self.lastSkillLevel != MAX_SKILL_LEVEL:
            raise ValueError, self.lastSkillLevel
        self.skills.append(skillName)
        self.lastSkillLevel = 0
        self.__levelUpLastSkill()
        return

    def dropSkill(self, skillName):
        idx = self.skills.index(skillName)
        numSkills = len(self.skills)
        if numSkills == 1:
            self.lastSkillLevel = 0
        elif idx + 1 == numSkills:
            self.lastSkillLevel = MAX_SKILL_LEVEL
        del self.skills[idx]
        return

    def respecialize(self, newVehicleTypeID, minNewRoleLevel, vehicleChangeRoleLevelLoss, classChangeRoleLevelLoss, becomesPremium):
        assert 0 <= minNewRoleLevel <= MAX_SKILL_LEVEL
        assert 0.0 <= vehicleChangeRoleLevelLoss <= 1.0
        assert 0.0 <= classChangeRoleLevelLoss <= 1.0
        from items import vehicles
        newVehTags = vehicles.g_list.getList(self.nationID)[newVehicleTypeID]['tags']
        roleLevelLoss = 0.0 if newVehicleTypeID == self.vehicleTypeID else vehicleChangeRoleLevelLoss
        isSameClass = len(self.__vehicleTags & newVehTags & vehicles.VEHICLE_CLASS_TAGS)
        if not isSameClass:
            roleLevelLoss += classChangeRoleLevelLoss
        newRoleLevel = int(round(self.roleLevel * (1.0 - roleLevelLoss)))
        newRoleLevel = max(minNewRoleLevel, newRoleLevel)
        self.vehicleTypeID = newVehicleTypeID
        self.__vehicleTags = newVehTags
        if newRoleLevel > self.roleLevel:
            self.__updateRankAtSkillLevelUp(newRoleLevel - self.roleLevel)
            self.roleLevel = newRoleLevel
            self.freeXp = 0
        elif newRoleLevel < self.roleLevel:
            self.numLevelsToNextRank += self.roleLevel - newRoleLevel
            self.roleLevel = newRoleLevel
            self.addXP(0)
        return

    def replacePassport(self, isFemale=None, firstNameID=None, lastNameID=None, iconID=None):
        if isFemale is None:
            isFemale = self.isFemale
        if firstNameID is None:
            firstNameID = self.firstNameID
        if lastNameID is None:
            lastNameID = self.lastNameID
        if iconID is None:
            iconID = self.iconID
        isOk = False
        config = getNationConfig(self.nationID)
        for group in config['normalGroups'] + config['premiumGroups']:
            if bool(group['isFemales']) != bool(isFemale):
                continue
            if firstNameID not in group['firstNames']:
                continue
            if lastNameID in group['lastNames'] and iconID in group['icons']:
                isOk = True
            break

        if isOk:
            self.isFemale = isFemale
            self.firstNameID = firstNameID
            self.lastNameID = lastNameID
            self.iconID = iconID
        return isOk

    def makeCompactDescr(self):
        pack = struct.pack
        header = items.ITEM_TYPE_INDICES['tankman'] + (self.nationID << 4)
        cd = pack('4B', header, self.vehicleTypeID, SKILL_INDICES[self.role], self.roleLevel)
        numSkills = len(self.skills)
        skills = [_[1] for s in self.skills]
        cd += pack((str(1 + numSkills) + 'B'), numSkills, *skills)
        cd += chr(self.lastSkillLevel if numSkills else 0)
        isFemale = 1 if self.isFemale else 0
        isPremium = 1 if self.isPremium else 0
        flags = isFemale | isPremium << 1
        cd += pack('<B3H2Bi', flags, self.firstNameID, self.lastNameID, self.iconID, self.__rankIdx, self.numLevelsToNextRank, self.freeXP)
        cd += self.dossierCompactDescr
        return cd

    def __initFromCompactDescr(self, compactDescr, battleOnly):
        cd = compactDescr
        unpack = struct.unpack
        try:
            (header, self.vehicleTypeID, roleID, self.roleLevel, numSkills) = unpack('5B', cd[:5])
            cd = cd[5:]
            assert header & 15 == items.ITEM_TYPE_INDICES['tankman']
            nationID = header >> 4 & 15
            nations.NAMES[nationID]
            self.nationID = nationID
            from items import vehicles
            self.__vehicleTags = vehicles.g_list.getList(nationID)[self.vehicleTypeID]['tags']
            self.role = SKILL_NAMES[roleID]
            if self.role not in ROLES:
                raise KeyError, self.role
            if self.roleLevel > MAX_SKILL_LEVEL:
                raise ValueError, self.roleLevel
            self.skills = []
            if numSkills == 0:
                self.lastSkillLevel = 0
            else:
                for skillID in unpack(str(numSkills) + 'B', cd[:numSkills]):
                    skillName = SKILL_NAMES[skillID]
                    if skillName not in COMMON_SKILLS:
                        PERKS_BY_ROLES[self.role][skillName]
                    self.skills.append(skillName)

                self.lastSkillLevel = ord(cd[numSkills])
                if self.lastSkillLevel > MAX_SKILL_LEVEL:
                    raise ValueError, self.lastSkillLevel
            cd = cd[numSkills + 1:]
            if battleOnly:
                return
            nationConfig = getNationConfig(nationID)
            (flags, self.firstNameID, self.lastNameID, self.iconID) = unpack('<B3H', cd[:7])
            (self.__rankIdx, self.numLevelsToNextRank, self.freeXP) = unpack('<2Bi', cd[7:13])
            cd = cd[13:]
            self.dossierCompactDescr = cd
            self.isFemale = bool(flags & 1)
            self.isPremium = bool(flags & 2)
            self.rankID = nationConfig['roleRanks'][self.role][self.__rankIdx]
            if self.firstNameID not in nationConfig['firstNames']:
                raise KeyError, self.firstNameID
            if self.lastNameID not in nationConfig['lastNames']:
                raise KeyError, self.lastNameID
            if self.iconID not in nationConfig['icons']:
                raise KeyError, self.iconID
        except Exception:
            LOG_ERROR('(compact description to XML mismatch?)', compactDescr)
            raise

        return

    def __paramsOnVehicle(self, vehicleType):
        isPremium = 'premium' in vehicleType.tags
        isSameClass = len(VEHICLE_CLASS_TAGS & vehicleType.tags & self.__vehicleTags)
        return (isPremium, isSameClass)

    def __updateRankAtSkillLevelUp(self, numLevels=1):
        if numLevels < self.numLevelsToNextRank:
            self.numLevelsToNextRank -= numLevels
            return
        rankIDs = getNationConfig(self.nationID)['roleRanks'][self.role]
        maxRankIdx = len(rankIDs) - 1
        while numLevels >= self.numLevelsToNextRank > 0:
            numLevels -= self.numLevelsToNextRank
            self.__rankIdx = min(self.__rankIdx + 1, maxRankIdx)
            self.rankID = rankIDs[self.__rankIdx]
            self.numLevelsToNextRank = SKILL_LEVELS_PER_RANK if self.__rankIdx < maxRankIdx else 0

        return

    def __levelUpLastSkill(self):
        while self.lastSkillLevel < MAX_SKILL_LEVEL:
            xpCost = self.levelUpXpCost(self.lastSkillLevel, len(self.skills))
            if xpCost > self.freeXP:
                break
            self.freeXP -= xpCost
            self.lastSkillLevel += 1
            self.__updateRankAtSkillLevelUp()

        return


def _readSkillsConfig(xmlPath):
    section = ResMgr.openSection(xmlPath)
    if section is None:
        _xml.raiseWrongXml(None, xmlPath, 'can not open or read')
    xmlCtx = (None, xmlPath)
    res = {}
    for skillName in ROLES:
        res[skillName] = _readSkill(xmlCtx, section, 'roles/' + skillName)

    for skillName in COMMON_SKILLS:
        res[skillName] = _readSkill(xmlCtx, section, 'commonSkills/' + skillName)

    section = None
    ResMgr.purge(xmlPath, True)
    return res


def _readSkill(xmlCtx, section, subsectionName):
    section = _xml.getSubsection(xmlCtx, section, subsectionName)
    xmlCtx = (xmlCtx, subsectionName)
    res = {'influence': (_xml.readFraction(xmlCtx, section, 'influence'))}
    if IS_CLIENT:
        res['userString'] = i18n.makeString(section.readString('userString'))
        res['description'] = i18n.makeString(section.readString('description'))
        res['icon'] = _xml.readNonEmptyString(xmlCtx, section, 'icon')
    return res


def _readNationConfig(xmlPath):
    section = ResMgr.openSection(xmlPath)
    if section is None:
        _xml.raiseWrongXml(None, xmlPath, 'can not open or read')
    res = _readNationConfigSection((None, xmlPath), section)
    section = None
    ResMgr.purge(xmlPath, True)
    return res


def _readNationConfigSection(xmlCtx, section):
    res = {}
    firstNames = {}
    lastNames = {}
    icons = {}
    for kindName in ('normalGroups', 'premiumGroups'):
        groups = []
        res[kindName] = groups
        totalWeight = 0.0
        for (sname, subsection) in _xml.getChildren(xmlCtx, section, kindName):
            ctx = (
             xmlCtx, kindName + '/' + sname)
            group = {'isFemales': ('female' == _xml.readNonEmptyString(ctx, subsection, 'sex')), 
               'firstNames': (_readIDs((
                            ctx, 'firstNames'), _xml.getChildren(ctx, subsection, 'firstNames'), firstNames, _parseName)), 
               'lastNames': (_readIDs((
                           ctx, 'lastNames'), _xml.getChildren(ctx, subsection, 'lastNames'), lastNames, _parseName)), 
               'icons': (_readIDs((
                       ctx, 'icons'), _xml.getChildren(ctx, subsection, 'icons'), icons, _parseIcon))}
            group['firstNamesList'] = list(group['firstNames'])
            group['lastNamesList'] = list(group['lastNames'])
            group['iconsList'] = list(group['icons'])
            weight = _xml.readNonNegativeFloat(ctx, subsection, 'weight')
            totalWeight += weight
            group['weight'] = weight
            groups.append(group)

        totalWeight = max(0.001, totalWeight)
        for group in groups:
            group['weight'] /= totalWeight

    (ranks, rankIDsByNames) = _readRanks((xmlCtx, 'ranks'), _xml.getChildren(xmlCtx, section, 'ranks'))
    res['roleRanks'] = _readRoleRanks((
     xmlCtx, 'roleRanks'), _xml.getSubsection(xmlCtx, section, 'roleRanks'), rankIDsByNames)
    if IS_CLIENT:
        res['firstNames'] = firstNames
        res['lastNames'] = lastNames
        res['icons'] = icons
        res['ranks'] = ranks
    else:
        res['firstNames'] = frozenset(firstNames)
        res['lastNames'] = frozenset(lastNames)
        res['icons'] = frozenset(icons)
    return res


def _readRanks(xmlCtx, subsections):
    ranks = []
    rankIDsByNames = {}
    for (sname, subsection) in subsections:
        if rankIDsByNames.has_key(sname):
            _xml.raiseWrong(xmlCtx, sname, 'is not unique')
        ctx = (xmlCtx, sname)
        rankIDsByNames[sname] = len(ranks)
        if not IS_CLIENT:
            ranks.append(None)
        else:
            ranks.append({'userString': (i18n.makeString(_xml.readNonEmptyString(ctx, subsection, 'userString'))), 
               'icon': (_parseIcon((ctx, 'icon'), _xml.getSubsection(ctx, subsection, 'icon')))})

    return (ranks, rankIDsByNames)


def _readRoleRanks(xmlCtx, section, rankIDsByNames):
    res = {}
    for roleName in ROLES:
        rankIDs = []
        res[roleName] = rankIDs
        for rankName in _xml.readNonEmptyString(xmlCtx, section, roleName).split():
            rankIDs.append(rankIDsByNames[rankName])

    return res


def _readIDs(xmlCtx, subsections, accumulator, parser):
    res = set()
    for (sname, subsection) in subsections:
        try:
            id = int(sname[1:])
        except Exception:
            id = -1

        if sname[0] != '_' or not 0 <= id <= 65535:
            _xml.raiseWrongSection(xmlCtx, sname)
        if id in accumulator:
            _xml.raiseWrongXml(xmlCtx, sname, 'ID is not unique')
        accumulator[id] = parser((xmlCtx, sname), subsection)
        res.add(id)

    if not res:
        _xml.raiseWrongXml(xmlCtx, '', 'is empty')
    return res


if not IS_CLIENT:

    def _parseName(xmlCtx, section):
        return


    _parseIcon = _parseName
else:

    def _parseName(xmlCtx, section):
        return i18n.makeString(_xml.readNonEmptyString(xmlCtx, section, ''))


    def _parseIcon(xmlCtx, section):
        return _xml.readNonEmptyString(xmlCtx, section, '')


def _computeSummSkillLevels(vehicleDescr, tankmanDescrs, levelIncrease):
    res = dict.fromkeys(ROLES_AND_COMMON_SKILLS, 0)
    for (descr, roles) in zip(tankmanDescrs, vehicleDescr.type.crewRoles):
        if descr is None:
            continue
        assert descr.role == roles[0]
        (factor, addition) = descr.efficiencyOnVehicle(vehicleDescr)
        addition += levelIncrease
        skillLevel = int(round(descr.roleLevel * factor)) + addition
        for skillName in roles:
            res[skillName] += skillLevel

        for skillName in descr.skills:
            if skillName not in COMMON_SKILLS:
                continue
            skillLevel = MAX_SKILL_LEVEL if skillName != descr.skills[-1] else descr.lastSkillLevel
            res[skillName] += int(round(skillLevel * factor)) + addition

    return res


_g_skillsConfig = None
_g_nationsConfig = [_[1] for x in xrange(len(nations.NAMES))]
return
