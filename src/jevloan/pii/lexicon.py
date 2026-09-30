"""Name lexicons and vocabulary lists for the `person_name` detector.

All Latin entries are lower-case; matching is done on the lower-cased token. The lists are deliberately biased
towards common names: the lexicon is a backstop behind the Redactor's exact known-entity replacement, not a
census. Names that are also ordinary English words live in `AMBIGUOUS_NAMES`: they extend a name sequence but
never anchor one, so "Grace period" and "Gold loan" are never flagged while "Mark Sharma" is.
"""

from __future__ import annotations


def _words(blob: str) -> frozenset[str]:
    return frozenset(blob.split())


# --------------------------------------------------------------------------------------------------
# Latin-script first names (all regions, all genders)
# --------------------------------------------------------------------------------------------------

_MALE_NORTH = """
aarav aarush abhay abhijeet abhinav abhishek aditya ajay ajit akash akhil akshay alok amar ambar amit amitabh
amitesh anand anil anirudh ankit ankur anmol anshul anup anurag arjun arnav arun arvind ashish ashok ashwin atul
avinash ayush bharat bhaskar bhavesh bhupendra brijesh chandan chandra chetan chirag darshan deepak devendra
dhruv dilip dinesh divyanshu gagan gaurav girish gopal govind gulshan hardik harish harsh hemant himanshu hitesh
inder ishaan ishan jagdish jai jatin jayant jitendra kailash kamal kapil karan kartik keshav kishan kishore kunal
lalit lokesh madan madhav mahendra mahesh manish manoj mayank milind mohan mohit mukesh mukul naman narendra
naresh naveen neeraj nikhil nilesh nirmal nishant nitin nitish pankaj paras parth pawan piyush pradeep pramod
pranav prashant pratik pratap praveen prem puneet rahul rajat rajeev rajendra rajesh rajiv rakesh ramesh ranjan
ranjit ravi ravindra rishabh rohan rohit ronak sachin sagar sahil sameer sandeep sanjay sanjeev santosh saurabh
shailesh shashank shivam shubham siddharth sohan sourabh subhash sudhir sumit sunil suraj suresh tarun tushar
uday umesh utkarsh vaibhav varun vijay vikas vikram vinay vinod vipin vishal vishnu vivek yash yogesh yuvraj
devansh kabir laksh reyansh vihaan advait aryan kshitij lakshya nakul sahadev yudhishthir bhim
"""

_FEMALE_NORTH = """
aarti aditi akanksha alka amrita anita anjali anjana ankita anushka anuradha archana arpita asha barkha bhavna
bhawna chhaya deepa deepika divya disha ekta garima gauri gayatri geeta geetha gita heena hema indira isha
jyoti kajal kalpana kamini kanchan kavita kiran komal kriti lata lakshmi laxmi madhu madhuri mala mamta manisha
meena meera megha mona monika mukta nandini neelam neha nidhi nisha nivedita pallavi payal pooja poonam prachi
pratibha preeti priya priyanka puja pushpa radha rachna rani rashmi reena rekha renu richa ritu riya rupal
sakshi sangeeta sapna sarita savita seema shalini sharmila shikha shilpa shobha shraddha shreya shruti shweta
simran sneha sonal sonali sonam sonia sunita supriya sushma swati tanvi tanya tripti uma urmila usha vandana
varsha vidya vinita yamini aishwarya ananya anvi diya kavya khushi kritika lavanya mansi mahima muskan navya
nikita pari pragya pranjali radhika rhea rimjhim ruchi sanya saanvi shivani shivangi srishti stuti trisha
vaani vartika yashika zara
"""

_SOUTH = """
aravind arumugam balaji balakrishnan bharathi bhuvan chandrasekhar dhanush ganesh gopalakrishnan hari harini
karthik karthikeyan kumaran mahalakshmi mani muthu murugan nagarajan narayan narayanan prakash rajkumar
ramakrishnan ramya ramaswamy saravanan selvam senthil sriram srinivas srinivasan subramanian sundar sundaram
thiru venkat venkatesh vignesh vijayakumar abirami anitha bhavani gayathri janani kalyani keerthi kokila
meenakshi nithya padma padmini pavithra revathi rohini saranya shobana sowmya sridevi subbulakshmi swetha
thenmozhi vaishnavi vasanthi vijaya chaitanya harsha madhusudhan nagaraju pavan ramana rajasekhar sudheer venu
vamsi lakshman biju jayan jithin manu rajan sabu sajeev sanal shibu unni vineeth aiswarya anju athira bindu
jisha lekha remya sindhu smitha sreeja soumya vidhya deepthi mythili sharanya sudha tamilselvi ilakkiya
"""

_EAST_WEST = """
abhijit anirban arnab ayan biswajit debashish dibyendu dipak gautam indranil kaushik partha prosenjit rabindra
saikat soumitra subhajit subrata sudipta sudipto swapan tapan arpita anwesha debjani ipsita madhumita mitali
moumita paromita rupa sharmistha sohini sumana sutapa tanushree amol ajinkya bhavin chintan dhaval dharmesh
hiren jignesh jayesh ketan kirit kalpesh mitesh nirav paresh pravin rushikesh swapnil vipul omkar rutuja snehal
yogita vaishali vrushali madhavi manasi mrunal dipti hetal komalben kinjal mansukh nilam pinal rajni
bhumika chandni damyanti hina hemlata jagruti jhanvi
"""

_MUSLIM = """
aamir abdul abid afzal ahmed aijaz akbar akram altaf amjad anwar arif arshad asif ayaan aziz faisal farhan
farooq fahad firoz hamid haroon hasan hussain imran iqbal irfan ismail jamal javed kamran khalid mansoor
mohammad mohammed mohd mubarak mujeeb muhammad mustafa nadeem naseem nasir naushad nawaz parvez rafiq rahim
rahman rashid rizwan sajid saleem salman sarfaraz shahid shahrukh shakeel shoaib sohail tariq usman wahid wasim
yakub yasin yusuf zaheer zakir zubair aaliya afreen ayesha farida fatima fauzia gulnaz haseena humera iram
khadija mehnaz mumtaz nargis naseema nazia nazneen noor nusrat parveen rabia rehana rukhsar sabina sadia saima
shabana shagufta shaista shazia sultana tabassum zainab zarina zeenat zoya
"""

_SIKH = """
amandeep amarjeet amarjit arshdeep balbir balwinder baljit gagandeep gurdeep gurmeet gurpreet gursharan
harbhajan hardeep harjeet harjit harmeet harpreet inderjeet jagjit jaskaran jaspreet jaswinder kamaljit
kuldeep kulwinder lakhwinder manjeet manjit manpreet navdeep navjot paramjit parminder rajinder rajwinder
ranbir satnam sukhbir sukhdev sukhwinder surinder tejinder simranjeet harleen jasleen kiranjit manjot navneet
gurleen rupinder
"""

_CHRISTIAN = """
anthony francis jacob joseph george mathew matthew philip peter sebastian thomas xavier varghese cyril dominic
gerald lawrence maria mary elizabeth rebecca priscilla teresa theresa angela flavia jessy sheela stella
shirley annie susan sarah rachel ruth martha agnes veronica cherian eapen abraham
"""

FIRST_NAMES: frozenset[str] = _words(
    _MALE_NORTH + _FEMALE_NORTH + _SOUTH + _EAST_WEST + _MUSLIM + _SIKH + _CHRISTIAN
)

# Names that are also everyday English words. They may extend a name sequence ("Mark Sharma") but they never
# anchor one and are never flagged on their own.
AMBIGUOUS_NAMES: frozenset[str] = _words("""
joy hope rose mark will bill sunny gold rich pat sunday honey happy lucky pearl ruby coral daisy lily faith
mercy charity grace dawn amber jasmine may june april august jan mar ram
""")
# Words such as "Ram Nagar" or "Sagar Road" (a name used as a place) are handled by NAME_TERMINATORS below.
FIRST_NAMES = FIRST_NAMES - AMBIGUOUS_NAMES

# --------------------------------------------------------------------------------------------------
# Surnames
# --------------------------------------------------------------------------------------------------

SURNAMES: frozenset[str] = _words("""
sharma verma gupta singh kumar yadav mishra pandey tiwari dubey shukla joshi agarwal agrawal aggarwal bansal
mittal goel goyal garg singhal jain khan ali ahmed hussain sheikh ansari qureshi malik siddiqui patel shah
desai mehta modi trivedi dave bhatt thakkar parekh vora kothari doshi chokshi gandhi kulkarni deshmukh
deshpande patil pawar shinde jadhav gaikwad chavan sawant bhosale kadam kale thorat bhat rao naidu reddy raju
chowdary chaudhary chaudhari choudhary iyer iyengar nair menon pillai kurup nambiar warrier panicker varma
krishnan murthy swamy subramanian ramachandran balasubramanian venkataraman raman natarajan srinivasan
sundaram rajan chandran gowda hegde shetty kamath pai prabhu naik dsouza d'souza pereira rodrigues fernandes
gomes lobo mascarenhas menezes mathew joseph george thomas philip varghese abraham cherian eapen jacob ghosh
banerjee banerji mukherjee mukherji chatterjee chatterji chakraborty chakrabarti bhattacharya bhattacharjee
dutta datta das dasgupta sen sengupta sarkar bose basu roy mondal biswas saha pal paul majumdar mazumdar sinha
jha prasad ranjan chauhan rathore rathod thakur rajput kapoor kapur khanna malhotra arora chopra oberoi sethi
sood anand bhatia grover batra sabharwal mahajan bedi talwar kohli tandon saxena srivastava bhardwaj
chaturvedi dwivedi kaushik dhawan walia sehgal suri tuli vij khurana luthra mehra mehrotra nagpal narula puri
sachdeva sahni seth kaur gill sandhu dhillon sidhu brar cheema grewal bains randhawa virk bajwa mann atwal
ahluwalia bhullar chahal kang toor lamba rawat negi bisht pant kandpal semwal uniyal nautiyal panwar tomar
solanki parmar vaghela chudasama zala jethwa lakhani mistry bhagat lodha maheshwari khandelwal soni sahu sahoo
mohanty pattnaik panda behera swain dash nayak jena mahapatra tripathi upadhyay ojha awasthi bajpai
shrivastava dixit chaubey pathak gautam kushwaha maurya kashyap prajapati vishwakarma paswan manjhi mandal
hazarika gogoi baruah saikia thapa gurung tamang subba limbu ramamurthy narasimhan raghavan seshadri
padmanabhan gopalan kannan parthasarathy ramanathan swaminathan viswanathan mukhopadhyay lahiri dhar
ganguly sanyal bagchi mitra chandra rastogi bhargava nigam mathur bhandari gulati anthony d'costa dcosta
fonseca noronha dias almeida pinto cardoz
""")

# Any Latin token that can anchor a name sequence.
NAME_ANCHORS: frozenset[str] = FIRST_NAMES | SURNAMES

# --------------------------------------------------------------------------------------------------
# Native-script names
# --------------------------------------------------------------------------------------------------

# Latin form -> Devanagari form for common names, so the Redactor can also catch the Devanagari rendering of
# a known Latin-script name. The Devanagari lexicon set below is derived from these plus extras.
LATIN_TO_DEVANAGARI: dict[str, str] = {
    "ramesh": "रमेश", "suresh": "सुरेश", "mahesh": "महेश", "dinesh": "दिनेश", "rajesh": "राजेश",
    "anil": "अनिल", "sunil": "सुनील", "sanjay": "संजय", "ajay": "अजय", "vijay": "विजय",
    "amit": "अमित", "sumit": "सुमित", "rohit": "रोहित", "mohit": "मोहित", "rahul": "राहुल",
    "vikas": "विकास", "vishal": "विशाल", "vivek": "विवेक", "prakash": "प्रकाश", "pradeep": "प्रदीप",
    "deepak": "दीपक", "ashok": "अशोक", "manoj": "मनोज", "mukesh": "मुकेश", "naresh": "नरेश",
    "neeraj": "नीरज", "nitin": "नितिन", "pankaj": "पंकज", "praveen": "प्रवीण", "pramod": "प्रमोद",
    "rakesh": "राकेश", "rajiv": "राजीव", "ravi": "रवि", "sandeep": "संदीप", "satish": "सतीश",
    "sachin": "सचिन", "saurabh": "सौरभ", "shailesh": "शैलेश", "shivam": "शिवम", "siddharth": "सिद्धार्थ",
    "umesh": "उमेश", "yogesh": "योगेश", "gaurav": "गौरव", "gopal": "गोपाल", "harish": "हरीश",
    "hemant": "हेमंत", "jitendra": "जितेंद्र", "kapil": "कपिल", "karan": "करण", "kunal": "कुणाल",
    "lokesh": "लोकेश", "manish": "मनीष", "mayank": "मयंक", "naveen": "नवीन", "nikhil": "निखिल",
    "piyush": "पीयूष", "prashant": "प्रशांत", "rajat": "रजत", "ranjit": "रंजीत", "sudhir": "सुधीर",
    "subhash": "सुभाष", "suraj": "सूरज", "tarun": "तरुण", "varun": "वरुण", "vinod": "विनोद",
    "vikram": "विक्रम", "yash": "यश", "priya": "प्रिया", "priyanka": "प्रियंका", "pooja": "पूजा",
    "neha": "नेहा", "seema": "सीमा", "sunita": "सुनीता", "anita": "अनीता", "anjali": "अंजलि",
    "kavita": "कविता", "mamta": "ममता", "meena": "मीना", "rekha": "रेखा", "reena": "रीना",
    "ritu": "रितु", "shalini": "शालिनी", "shweta": "श्वेता", "sonam": "सोनम", "swati": "स्वाति",
    "divya": "दिव्या", "bhavna": "भावना", "geeta": "गीता", "sarita": "सरिता", "savita": "सविता",
    "sangeeta": "संगीता", "suman": "सुमन", "jyoti": "ज्योति", "sharma": "शर्मा", "verma": "वर्मा",
    "gupta": "गुप्ता", "singh": "सिंह", "yadav": "यादव", "kumar": "कुमार", "mishra": "मिश्रा",
    "pandey": "पांडेय", "tiwari": "तिवारी", "dubey": "दुबे", "shukla": "शुक्ला", "joshi": "जोशी",
    "agarwal": "अग्रवाल", "bansal": "बंसल", "chauhan": "चौहान", "rathore": "राठौर", "thakur": "ठाकुर",
    "kapoor": "कपूर", "khanna": "खन्ना", "malhotra": "मल्होत्रा", "arora": "अरोड़ा", "saxena": "सक्सेना",
    "srivastava": "श्रीवास्तव", "bhardwaj": "भारद्वाज", "trivedi": "त्रिवेदी", "patel": "पटेल",
    "desai": "देसाई", "mehta": "मेहता", "kulkarni": "कुलकर्णी", "deshmukh": "देशमुख", "patil": "पाटिल",
    "pawar": "पवार", "shinde": "शिंदे", "jadhav": "जाधव", "maurya": "मौर्य", "kushwaha": "कुशवाहा",
    "prajapati": "प्रजापति", "paswan": "पासवान", "khan": "खान", "ansari": "अंसारी",
}  # fmt: skip

DEVANAGARI_NAMES: frozenset[str] = frozenset(LATIN_TO_DEVANAGARI.values()) | _words("""
राम कृष्ण लक्ष्मण भरत हनुमान रामेश्वर रविंद्र राजेंद्र महेंद्र सुरेंद्र नरेंद्र धर्मेंद्र वीरेंद्र देवेंद्र
अभिषेक आदित्य आकाश अर्जुन अरुण अरविंद आशीष अतुल अभय अभिनव ऋषभ चिराग गिरीश गोविंद हर्ष जगदीश
जयंत कमल केशव किशोर मदन माधव मनमोहन मुकुल नंदकिशोर निर्मल पवन परेश पुनीत रजनीश रोहन
सोनिया शिखा शिल्पा श्रद्धा श्रेया सुषमा सुप्रिया उर्मिला उषा वंदना वर्षा विद्या वनिता रश्मि राधा राखी
रचना ऋचा निधि नीलम निशा पल्लवी पायल प्रीति पुष्पा लता कामिनी कंचन कल्पना अलका अनुराधा अर्चना
अल्पना आरती अदिति अनन्या इंदिरा इशा गायत्री गरिमा कोमल
""")

# Tamil, Bengali, Telugu, Kannada, Malayalam, Gujarati: fewer entries; label and honorific rules do most of
# the work for these scripts. Mis-spellings here only lose recall, never cause a false positive.
OTHER_NATIVE_NAMES: frozenset[str] = _words("""
ராஜேஷ் சுரேஷ் ரமேஷ் குமார் முருகன் செல்வம் ராஜா கார்த்திக் வெங்கடேஷ் லட்சுமி பிரியா மீனா கவிதா ஸ்ரீதேவி
சரவணன் சுப்பிரமணியன் பாலாஜி கிருஷ்ணன் ராமசாமி அருண் அஜய் விஜய் சுந்தர் கணேஷ் திவ்யா அனிதா
রাহুল সুমন অনিল সুনীল দীপক সৌরভ অভিজিৎ প্রিয়া সুমিতা রুমা ঝুমা শর্মিষ্ঠা মৌসুমী অর্পিতা দেবযানী সুব্রত অরূপ
তাপস পার্থ বিশ্বজিৎ রণজিৎ সন্দীপ সঞ্জয় কৌশিক ঘোষ দাস সেন বসু রায় মুখার্জী চ্যাটার্জী ব্যানার্জী ভট্টাচার্য
চক্রবর্তী সরকার দত্ত পাল সাহা বিশ্বাস মণ্ডল
సురేష్ రమేష్ వెంకటేష్ శ్రీనివాస్ లక్ష్మి ప్రియ రెడ్డి నాయుడు కృష్ణ రాజు గోపాల్ సత్యనారాయణ
ಸುರೇಶ್ ರಮೇಶ್ ವೆಂಕಟೇಶ್ ಶ್ರೀನಿವಾಸ್ ಲಕ್ಷ್ಮಿ ಪ್ರಿಯಾ ಗೌಡ
രാജേഷ് സുരേഷ് രമേഷ് നായർ മേനോൻ പിള്ള
રમેશ સુરેશ મહેશ પટેલ શાહ દેસાઈ મહેતા
""")

# --------------------------------------------------------------------------------------------------
# Markers and vocabulary used by the detector
# --------------------------------------------------------------------------------------------------

# Words that end a name sequence: they mark the token before them as part of a place or business name
# ("Gandhi Road", "Sharma Traders"), not a person.
NAME_TERMINATORS: frozenset[str] = _words("""
road rd street st nagar marg colony layout chowk bazaar bazar market enclave vihar puram pur garden gardens park
lane society apartments apartment heights towers tower plaza complex circle cross main stage sector phase
extension hospital university college school institute trust foundation bank ltd limited pvt private llp inc
corp corporation company co traders trading enterprises enterprise industries textiles motors finance steel
foods stores store agency agencies associates associate services solutions technologies technology systems
infotech labs laboratories pharma pharmaceuticals builders developers constructions construction engineers
engineering electricals electronics exports imports mills mill sons brothers bros partners group holdings
works workshop clinic nursing hotel restaurant bhawan bhavan mandir temple masjid church gurudwara salon
sweets dairy transport travels logistics auto garage medical electric hardware jewellers jewelers opticals
fashion garments printers press studio academy classes coaching tutorials chambers towers house villa villas
residency residences estate estates township nivas sadan kunj bagh talab ganj gunj pura wadi wada peth
""")

# Capitalised words that are never part of a personal name in loan documents.
NON_NAME_CAPS: frozenset[str] = _words("""
the a an of and or for to in on at by is are was from with as no not nil na n/a applicant co-applicant borrower
guarantor customer consumer employee employer proprietor director partner owner tenant landlord holder account
bank branch manager officer address proof income statement salary slip pay net gross basic allowance
deduction deductions total amount balance credit debit interest rate tenure loan emi limit outstanding opening
closing date month year period form certificate letter report valuation property title deed registration
business company firm gst gstin pan aadhaar passport voter licence license utility bill electricity water
telephone gas mobile rent agreement lease sale purchase bureau score band enquiries payment payments fee fees
charges late penalty penal processing sanction sanctioned disbursal disbursed memo kfs annual percentage cost
schedule repayment monitoring appraisal readiness fraud identity signals synthetic conduct capacity collateral
january february march april may june july august september october november december jan feb mar apr jun jul
aug sep sept oct nov dec monday tuesday wednesday thursday friday saturday sunday mon tue wed thu fri sat sun
india indian rs inr yes true false none unknown redacted masked page section clause note notes see
please dear sir madam regards sincerely yours faithfully signed signature stamp seal authorised authorized
signatory approved verified checked reviewed prepared issued received submitted current previous last next
first second third fourth fifth sixth seventh eighth ninth tenth
""")

# Labels whose value is a person's name. Tier A labels accept any-case value tokens after a colon; tier B labels
# need a capitalised (or lexicon) value.
LABELS_A: tuple[str, ...] = (
    "full name", "first name", "last name", "middle name", "surname", "given name", "family name",
    "name", "applicant name", "applicant", "co-applicant name", "co-applicant", "coapplicant", "co applicant",
    "co-borrower name", "co-borrower", "borrower name", "borrower", "guarantor name", "guarantor",
    "proprietor name", "proprietor", "account holder name", "account holder", "a/c holder name",
    "a/c holder", "acct holder", "holder name", "employee name", "customer name", "consumer name",
    "father's name", "father name", "mother's name", "mother name", "spouse name", "spouse's name",
    "husband name", "husband's name", "wife name", "wife's name", "nominee name", "nominee",
    "beneficiary name", "beneficiary", "payee name", "payee", "signatory name", "authorised signatory",
    "authorized signatory", "director name", "partner name", "owner name", "tenant name", "landlord name",
    "lessor", "lessee", "drawer", "depositor", "account name", "name of applicant", "name of borrower",
    "name of employee", "name of the applicant", "name of the borrower", "name of account holder",
)  # fmt: skip
LABELS_B: tuple[str, ...] = (
    "employee", "customer", "consumer", "owner", "tenant", "landlord", "partner", "director", "holder",
    "signatory", "seller", "buyer", "father", "mother", "spouse", "husband", "wife", "attn", "attention",
)  # fmt: skip

# Native-script labels (tier A). A colon (ASCII or fullwidth, normalised) is required.
NATIVE_LABELS: tuple[str, ...] = (
    # Hindi / Marathi
    "आवेदक का नाम", "आवेदक", "खाताधारक का नाम", "खाताधारक", "पिता का नाम", "माता का नाम", "पति का नाम",
    "पत्नी का नाम", "कर्मचारी का नाम", "कर्मचारी", "उधारकर्ता का नाम", "उधारकर्ता", "उपभोक्ता", "ग्राहक का नाम",
    "ग्राहक", "मालिक", "प्रोपराइटर", "गारंटर", "नामांकित", "नाम", "नाव", "अर्जदाराचे नाव", "वडिलांचे नाव",
    # Tamil
    "விண்ணப்பதாரர் பெயர்", "விண்ணப்பதாரர்", "தந்தை பெயர்", "தாய் பெயர்", "கணக்கு வைத்திருப்பவர்", "பெயர்",
    # Bengali
    "আবেদনকারীর নাম", "আবেদনকারী", "পিতার নাম", "মাতার নাম", "স্বামীর নাম", "নাম",
    # Telugu, Kannada, Malayalam, Gujarati, Gurmukhi, Odia
    "దరఖాస్తుదారు పేరు", "పేరు", "ಹೆಸರು", "അപേക്ഷകന്റെ പേര്", "പേര്", "નામ", "ਨਾਮ", "ନାମ",
)  # fmt: skip

# Honorifics and relation markers in non-Latin scripts. They are followed by one to three native-script tokens.
NATIVE_HONORIFICS: tuple[str, ...] = (
    "श्रीमती", "श्रीमान", "सुश्री", "कुमारी", "श्री", "डॉ", "स्व", "पुत्र", "पुत्री", "पत्नी", "सुपुत्र", "सुपुत्री",
    "திருமதி", "திரு", "செல்வி", "டாக்டர்",
    "শ্রীমতী", "শ্রীযুক্ত", "শ্রী", "কুমারী", "জনাব", "ডঃ",
    "శ్రీమతి", "శ్రీ", "ಶ್ರೀಮತಿ", "ಶ್ರೀ", "ശ്രീമതി", "ശ്രീ", "શ્રીમતી", "શ્રી",
)  # fmt: skip

# Distinct Indian city and state names, used only to recognise "<City> - 560034" as the tail of an address.
PLACE_NAMES: tuple[str, ...] = (
    "mumbai", "navi mumbai", "delhi", "new delhi", "bengaluru", "bangalore", "hyderabad", "secunderabad", "chennai",
    "kolkata", "calcutta", "pune", "ahmedabad", "surat", "jaipur", "lucknow", "kanpur", "nagpur", "indore",
    "bhopal", "visakhapatnam", "vizag", "patna", "vadodara", "baroda", "ghaziabad", "ludhiana", "agra", "nashik",
    "faridabad", "meerut", "rajkot", "varanasi", "srinagar", "aurangabad", "dhanbad", "amritsar", "allahabad",
    "prayagraj", "ranchi", "howrah", "coimbatore", "jabalpur", "gwalior", "vijayawada", "jodhpur", "madurai",
    "raipur", "kota", "guwahati", "chandigarh", "solapur", "hubli", "hubballi", "mysuru", "mysore",
    "tiruchirappalli", "trichy", "bareilly", "thiruvananthapuram", "trivandrum", "kochi", "cochin", "kozhikode",
    "calicut", "thrissur", "gurugram", "gurgaon", "noida", "greater noida", "thane", "dehradun", "mangalore",
    "mangaluru", "bhubaneswar", "cuttack", "salem", "erode", "tirupati", "warangal", "guntur", "nellore",
    "belgaum", "belagavi", "jamshedpur", "bhilai", "ujjain", "jalandhar", "panaji", "shimla", "udaipur", "ajmer",
    "gorakhpur", "moradabad", "aligarh", "saharanpur", "kolhapur", "sangli", "nanded", "ambala", "vellore",
    "tiruppur", "bikaner", "jammu", "siliguri", "durgapur", "asansol", "bokaro", "rourkela", "sambalpur",
    "karnataka", "maharashtra", "tamil nadu", "kerala", "gujarat", "rajasthan", "uttar pradesh", "madhya pradesh",
    "west bengal", "telangana", "andhra pradesh", "punjab", "haryana", "bihar", "odisha", "jharkhand",
    "chhattisgarh", "assam", "uttarakhand", "himachal pradesh", "goa",
)  # fmt: skip

# English words that are also given names but are commonly written capitalised at a sentence start; the
# Redactor treats these parts of a known name as case-sensitive so it does not chew ordinary prose.
COMMON_ENGLISH_NAME_PARTS: frozenset[str] = AMBIGUOUS_NAMES | _words("""
king prince major victor art ray jay dean earl
""")
