import zipfile,xml.etree.ElementTree as ET
def make_package(path):
    cache=ET.Element('cache',{'id':'sales'});field=ET.SubElement(cache,'field',{'name':'region'})
    for value in ['B','C']:ET.SubElement(field,'s',{'v':value})
    slicer=ET.Element('slicer',{'field':'region','cache':'sales'});ET.SubElement(slicer,'selected',{'index':'1'})
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('cache/data.xml',ET.tostring(cache));z.writestr('slicers/filter.xml',ET.tostring(slicer));z.writestr('opaque/keep.bin',b'unchanged')
